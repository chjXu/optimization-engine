import yaml
import os
import subprocess
import socket
import json
import logging
import time
import math
import pkg_resources
from threading import Thread
from retry import retry
from .solver_response import SolverResponse


class OptimizerTcpManager:
    """Client for TCP interface of parametric optimizers

    This class is used to start and stop a TCP server, which
    has been generated by <code>opengen</code>.
    """

    def __init__(self, optimizer_path=None, ip=None, port=None):
        """Constructs instance of <code>OptimizerTcpManager</code>

        Args:
            optimizer_path: path to auto-generated optimizer (just to
            be clear: this is the folder that contains <code>optimizer.yml</code>)

        Returns:
            New instance of <code>OptimizerTcpManager</code>
        """
        self.__optimizer_path = optimizer_path
        if optimizer_path is not None:
            self.__optimizer_details_from_yml = None
            self.__load_tcp_details()
        elif ip is not None and port is not None:
            self.__optimizer_details_from_yml = {"tcp": {"ip": ip, "port": port}}
        else:
            raise Exception("Illegal arguments")
        # Check whether the optimizer was built with the current version of opengen
        opengen_version = self.__optimizer_details_from_yml['build']['opengen_version']
        current_opengen_version = pkg_resources.require("opengen")[0].version
        if current_opengen_version != opengen_version:
            logging.warn('the target optimizer was build with a different version of opengen (%s)' % opengen_version)
            logging.warn('you are running opengen version %s' % current_opengen_version)

    def __load_tcp_details(self):
        logging.info("loading TCP/IP details")
        yaml_file = os.path.join(self.__optimizer_path, "optimizer.yml")
        with open(yaml_file, 'r') as stream:
            self.__optimizer_details_from_yml = yaml.safe_load(stream)
        details = self.__optimizer_details_from_yml
        logging.info("TCP/IP details: %s:%d", details['tcp']['ip'], details['tcp']['port'])

    def __threaded_start(self):
        optimizer_details = self.__optimizer_details_from_yml
        logging.info("Starting TCP/IP server at %s:%d (in a detached thread)",
                     optimizer_details['tcp']['ip'],
                     optimizer_details['tcp']['port'])
        command = ['cargo', 'run', '-q']
        if optimizer_details['build']['build_mode'] == 'release':
            command.append('--release')
        tcp_dir_name = "tcp_iface_" + optimizer_details['meta']['optimizer_name']
        tcp_iface_directory = os.path.join(self.__optimizer_path, tcp_dir_name)
        p = subprocess.Popen(command, cwd=tcp_iface_directory)
        p.wait()

    @retry(tries=10, delay=1)
    def __obtain_socket_connection(self):
        tcp_data = self.__optimizer_details_from_yml
        ip = tcp_data['tcp']['ip']
        port = tcp_data['tcp']['port']
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        s.connect((ip, port))
        return s

    def __send_receive_data(self, text_to_send, buffer_size=512, max_data_size=1048576):
        conn_socket = self.__obtain_socket_connection()
        encoded_data = text_to_send.encode()
        conn_socket.sendall(encoded_data)
        conn_socket.shutdown(socket.SHUT_WR)

        max_read_rounds = math.ceil(max_data_size/buffer_size)
        data = b''
        for _i in range(max_read_rounds):
            data_chunk = conn_socket.recv(buffer_size)
            if data_chunk is None:
                break
            data += data_chunk

        conn_socket.close()
        return data.decode()

    def ping(self):
        """Pings the server

        Pings the server to check whether it is up and running
        """
        request = '{"Ping":1}'
        data = self.__send_receive_data(request)
        return json.loads(data)

    def __check_if_server_is_running(self):
        tcp_data = self.__optimizer_details_from_yml
        ip = tcp_data['tcp']['ip']
        port = tcp_data['tcp']['port']
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        return 0 == s.connect_ex((ip, port))

    def start(self):
        """Starts the TCP server"""
        # start the server in a separate thread

        if self.__optimizer_path is None:
            raise Exception("No optimizer path provided - cannot start a remote server")

        if self.__check_if_server_is_running():
            msg = "Port %d not available" % self.__optimizer_details_from_yml['tcp']['port']
            raise Exception(msg)

        logging.info("Starting TCP/IP server thread")
        thread = Thread(target=self.__threaded_start)
        thread.start()

        # ping the server until it responds so that we know it's
        # up and running
        logging.info("Waiting for server to start")
        time.sleep(2)
        self.ping()

    def kill(self):
        """Kills the server"""
        logging.info("Killing server")
        request = '{"Kill":1}'
        self.__send_receive_data(request)

    def call(self, p, initial_guess=None,
             initial_y=None,
             initial_penalty=None,
             buffer_len=4096,
             max_data_size=1048576) -> SolverResponse:
        """Calls the server

        Consumes the parametric optimizer by providing a parameter vector
        and, optionally, an initial guess

        Args:
             p: vector of parameters (list of float)
             initial_guess: initial guess vector (list of float)
             initial_y: initial vector of Lagrange multipliers (list of float)
             initial_penalty: initial penalty parameter (float)
             buffer_len: buffer length used to read the server response
             (default value: 4096)
             max_data_size: maximum data size that is expected to be
             received from the TCP server (default value: 1048576)

        Returns:
            Instance of SolverResponse

        """
        # Make request
        logging.debug("Sending request to TCP/IP server")
        run_message = '{"Run" : {"parameter": ['
        run_message += ','.join(map(str, p))
        run_message += ']'

        if initial_guess is not None:
            run_message += ', "initial_guess": ['
            run_message += ','.join(map(str, initial_guess))
            run_message += ']'

        if initial_y is not None:
            run_message += ', "initial_lagrange_multipliers": ['
            run_message += ','.join(map(str, initial_y))
            run_message += ']'

        if initial_penalty is not None:
            run_message += ', "initial_penalty": ' + str(float(initial_penalty))

        run_message += '}}'
        data = self.__send_receive_data(run_message, buffer_len, max_data_size)
        return SolverResponse(json.loads(data))
