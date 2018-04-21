import logging
import socket
import numpy as np

from pydm.data_plugins.plugin import PyDMPlugin, PyDMConnection
from pydm.PyQt.QtCore import pyqtSignal, pyqtSlot, Qt, QThread, QTimer, QMutex
from pydm.PyQt.QtGui import QApplication
from pydm.utilities import is_pydm_app

from umodbus.client import tcp

logger = logging.getLogger(__name__)


class ModbusServer:
    sock_cache = {}

    def __init__(self, ip, port):
        if self.make_hash(ip, port) in ModbusServer.sock_cache:
            return
        self.ip = ip
        self.port = port
        self.sock = None
        logger.info("Will open server at: {}:{}".format(self.ip, self.port))
        self.mutex = QMutex()
        self.connected = False
        self.connect()
        self.alive_timer = QTimer()
        self.alive_timer.setInterval(1000)
        self.alive_timer.timeout.connect(self.check_alive)
        self.alive_timer.start()
        ModbusServer.sock_cache[self.make_hash(ip, port)] = self

    def __new__(cls, ip, port):
        obj_hash = ModbusServer.make_hash(ip, port)
        if obj_hash in cls.sock_cache:
            return ModbusServer.sock_cache[obj_hash]
        else:
            server = super(ModbusServer, cls).__new__(cls)
            return server

    def check_alive(self):
        self.alive_timer.stop()
        if not self.connected:
            self.connect()
        self.alive_timer.start()

    def connect(self):
        if self.connected:
            return

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.settimeout(0.5)
            self.sock.connect((self.ip, self.port))
            self.connected = True
        except Exception as ex:
            logger.error('Error connecting to socket. {}'.format(str(ex)))

    def send_message(self, message):
        self.mutex.lock()
        response = None
        if not self.connected:
            self.mutex.unlock()
            return response
        try:
            response = tcp.send_message(message, self.sock)
        except Exception as ex:
            logger.error("Error sending message to server. {}".format(str(ex)))
            if "timed out" in str(ex):
                self.mutex.unlock()
                return None
            self.connected = False
        self.mutex.unlock()
        return response

    def __hash__(self):
        return self.make_hash(self.ip, self.port)

    def __eq__(self, other):
        return (self.ip, self.port) == (other.ip, other.port)

    def __ne__(self, other):
        return not (self == other)

    @staticmethod
    def make_hash(ip, port):
        return hash((ip, port))


class DataThread(QThread):
    new_data_signal = pyqtSignal([float], [int], [str])

    def __init__(self, ip, port, slave_id, designator, addr, length, bit, poll_interval=0.1):
        super(QThread, self).__init__()
        self.ip = ip
        self.port = port
        self.slave_id = slave_id
        self.designator = designator
        self.addr = addr
        self.length = length
        self.bit = bit
        self.poll_interval = poll_interval

        self.designator_map = {
            'HR': {'read': self.read_hr, 'write': self.write_hr}
        }

        self.server = ModbusServer(self.ip, self.port)

        try:
            self.read_data = self.designator_map[self.designator]['read']
            self.write_data = self.designator_map[self.designator]['write']
        except IndexError:
            self.read_data = lambda *a, **k: None
            self.write_data = lambda *a, **k: None

    def run(self):
        while not self.isInterruptionRequested():
            data = self.read_data()
            if data is not None:
                self.new_data_signal.emit(data)
            self.msleep(int(self.poll_interval*1000))

    def write(self, new_value):
        self.write_data(new_value)

    def read_hr(self):
        message = tcp.read_holding_registers(slave_id=self.slave_id, starting_address=self.addr, quantity=1)
        response = self.server.send_message(message)

        if response is not None:
            if self.bit is None:
                return response[0]
            else:
                def get_bit(decimal, N):
                    mask = 1 << N
                    if decimal & mask:
                        return 1
                    else:
                        return 0
                return (get_bit(response[0],self.bit))

        else:
            return None


    def write_hr(self, new_value):

        if self.bit is not None:
            message = tcp.read_holding_registers(slave_id=self.slave_id, starting_address=self.addr, quantity=1)
            response = self.server.send_message(message)
            current_value =response[0]

            if new_value:
                new_value = current_value | 1 << self.bit
            else:
                new_value = current_value & 1 << self.bit

        message = tcp.write_multiple_registers(slave_id=self.slave_id, starting_address=self.addr, values=[int(new_value)])
        response = self.server.send_message(message)
        return response


class Connection(PyDMConnection):
    ADDRESS_FORMAT = "modbus://<ip>:<port>/<slave_id>:<type>:<address>:<lenght>.<bit>/<polling|0.1>"

    def __init__(self, channel, address, protocol=None, parent=None):
        super(Connection, self).__init__(channel, address, protocol, parent)
        self.app = QApplication.instance()

        # Default Values
        self.server = None
        self.ip = '127.0.0.1'
        self.port = '502'
        self.slave_id = 0
        self.designator = 'HR'
        self.addr = 0
        self.length = 0
        self.bit = None
        self.poll = 0.1  # 100 ms

        self.parse_address(address)

        self.add_listener(channel)

        self.data_thread = DataThread(self.ip, self.port, self.slave_id, self.designator, self.addr, self.length,
                                      self.bit, self.poll)
        self.data_thread.new_data_signal.connect(self.emit_data, Qt.QueuedConnection)
        self.data_thread.start()

        self.metadata_timer = QTimer()
        self.metadata_timer.setInterval(500)
        self.metadata_timer.timeout.connect(self.emit_metadata)
        self.metadata_timer.start()

    def parse_address(self, address):
        def check_for_bits(info):
            # Check if we have bits in it...
            if info.find(".") != -1:
                return info.split(".")
            else:
                return info, None

        data = address.split("/")
        # Parse IP and Port
        data_server = data[0].split(":")
        self.ip = data_server[0]
        self.port = int(data_server[1])

        # Parse slave_id, designator, address, length, bit
        data_data = data[1].split(":")
        if len(data_data) < 3:
            raise ValueError("Invalid Address. The format must be: {}".format(self.ADDRESS_FORMAT))
        self.slave_id = int(data_data[0])
        self.designator = data_data[1]
        self.addr, self.bit = check_for_bits(data_data[2])
        self.addr = int(self.addr)
        if self.bit is not None:
            self.bit = int(self.bit)

        # Check if we have length defined
        if len(data_data) > 3:
            self.length, self.bit = check_for_bits(data_data[3])

        # Check if we have polling defined
        if len(data) > 2:
            self.poll = float(data[2])

    def emit_metadata(self):
        self.emit_access_state()
        self.emit_connection_state(self.data_thread.server.connected)

    @pyqtSlot(int)
    @pyqtSlot(float)
    @pyqtSlot(str)
    @pyqtSlot(bool)
    def emit_data(self, new_data):
        if new_data is not None:
            self.new_value_signal[type(new_data)].emit(new_data)

    def emit_access_state(self):
        if is_pydm_app() and self.app.is_read_only():
            self.write_access_signal.emit(False)
            return

        self.write_access_signal.emit(True)

    def emit_connection_state(self, conn):
        if conn:
            self.connection_state_signal.emit(True)
        else:
            self.connection_state_signal.emit(False)

    @pyqtSlot(int)
    @pyqtSlot(float)
    @pyqtSlot(str)
    @pyqtSlot(np.ndarray)
    def put_value(self, new_val):
        if is_pydm_app() and self.app.is_read_only():
            return

        try:
            self.data_thread.write(new_val)
        except Exception as e:
            logger.error("Unable to put %s to %s.  Exception: %s",
                         new_val, self.address, str(e))

    def add_listener(self, channel):
        super(Connection, self).add_listener(channel)

        # If the channel is used for writing to PVs, hook it up to the 'put' methods.
        if channel.value_signal is not None:
            try:
                channel.value_signal[str].connect(self.put_value, Qt.QueuedConnection)
            except KeyError:
                pass
            try:
                channel.value_signal[int].connect(self.put_value, Qt.QueuedConnection)
            except KeyError:
                pass
            try:
                channel.value_signal[float].connect(self.put_value, Qt.QueuedConnection)
            except KeyError:
                pass
            try:
                channel.value_signal[np.ndarray].connect(self.put_value, Qt.QueuedConnection)
            except KeyError:
                pass

    def remove_listener(self, channel):
        if channel.value_signal is not None:
            try:
                channel.value_signal[str].disconnect(self.put_value)
            except KeyError:
                pass
            try:
                channel.value_signal[int].disconnect(self.put_value)
            except KeyError:
                pass
            try:
                channel.value_signal[float].disconnect(self.put_value)
            except KeyError:
                pass
            try:
                channel.value_signal[np.ndarray].disconnect(self.put_value)
            except KeyError:
                pass

        super(Connection, self).remove_listener(channel)

    def close(self):
        self.data_thread.requestInterruption()
        self.data_thread.terminate()


class ModbusPlugin(PyDMPlugin):
    # NOTE: protocol is intentionally "None" to keep this plugin from getting directly imported.
    # If this plugin is chosen as the One True EPICS Plugin in epics_plugin.py, the protocol will
    # be properly set before it is used.
    protocol = "modbus"
    connection_class = Connection
