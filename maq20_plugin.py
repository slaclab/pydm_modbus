import logging
import socket
import numpy as np

from pydm.data_plugins.plugin import PyDMPlugin, PyDMConnection
from pydm.PyQt.QtCore import pyqtSignal, pyqtSlot, Qt, QThread, QTimer, QMutex
from pydm.PyQt.QtGui import QApplication
from pydm.utilities import is_pydm_app

from maq20 import MAQ20


logger = logging.getLogger(__name__)


class Maq20Server(QThread):
    sock_cache = {}

    def __init__(self, ip, port):
        super(QThread, self).__init__()
        if self.make_hash(ip, port) in Maq20Server.sock_cache:
            return
        self.ip = ip
        self.port = port
        self.system = None
        logger.info("Will open MAQ20 server at: {}:{}".format(self.ip, self.port))
        self.mutex = QMutex()
        self.connected = False
        self.connect()
        self.start()
        Maq20Server.sock_cache[self.make_hash(ip, port)] = self

    def __new__(cls, ip, port):
        obj_hash = Maq20Server.make_hash(ip, port)
        if obj_hash in cls.sock_cache:
            return Maq20Server.sock_cache[obj_hash]
        else:
            server = super(Maq20Server, cls).__new__(cls)
            return server

    def run(self):

        while not self.isInterruptionRequested():
            if not self.connected:
                self.mutex.lock()
                self.connect()
                self.mutex.unlock()
            self.sleep(1)

    def connect(self):
        if self.connected:
            return

        try:
            self.system = None
            self.system = MAQ20(ip_address=self.ip, port=self.port)
            self.connected = True
            logger.info("Connected to MAQ20 server at: {}:{}".format(self.ip, self.port))
        except Exception as ex:
            logger.error('Error connecting to MAQ20. {}'.format(str(ex)))

    def disconnect(self):
        if not self.connected:
            return

        try:
            self.system = None
            self.connected = False
        except Exception as ex:
            logger.error('Error disconnecting from MAQ20. {}'.format(str(ex)))


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

    def __init__(self, ip, port, module, addr, poll_interval=0.1):
        super(QThread, self).__init__()
        self.ip = ip
        self.port = port

        if module.find('S') != 0:
            module = "S"+module
        self.module_sn = module

        self.module = None
        self.addr = addr
        self.poll_interval = poll_interval

        self.server = Maq20Server(self.ip, self.port)

    def run(self):
        while not self.isInterruptionRequested():
            if self.server.connected:
                    data = self.read_data()
                    if data is not None:
                        self.new_data_signal.emit(data)
            self.msleep(int(self.poll_interval*1000))

    def write(self, new_value):
        if self.server.connected:
            self.write_data(new_value)

    def read_data(self):
        self.server.mutex.lock()

        try:
            if self.module is None:
                print(self.module, self.ip, self.module_sn, self.addr)

                self.module = self.server.system.find(self.module_sn)

            if not self.module.has_range_information():
                response = self.module.read_channel_data_counts(int(self.addr))
            else:
                self.module.load_channel_active_ranges()
                response = self.module.read_channel_data(int(self.addr))

        except:
            self.module = None
            response = None
            try:
                self.server.system.time()
            except:
                self.server.connected = False
        finally:
            self.server.mutex.unlock()

        if response is not None:
                return response
        else:
            return None


    def write_data(self, new_value):
        self.server.mutex.lock()
        try:
            if self.module is None:
                self.module = self.server.system.find(self.module_sn)
            if not self.module.has_range_information():
                self.module.write_register(1000+int(self.addr),new_value)
            else:
                self.module.write_channel_data(int(self.addr),new_value)
        except:
            self.module = None
            try:
                self.server.system.time()
            except:
                self.server.connected = False
        finally:
            self.server.mutex.unlock()


class Connection(PyDMConnection):
    ADDRESS_FORMAT = "maq20://<ip>:<port>/<module_sn>:<address>/<polling|0.1>"

    def __init__(self, channel, address, protocol=None, parent=None):
        super(Connection, self).__init__(channel, address, protocol, parent)
        self.app = QApplication.instance()

        # Default Values
        self.server = None
        self.ip = '127.0.0.1'
        self.port = '502'
        self.module_sn = 0
        self.addr = 0
        self.poll = 0.1  # 100 ms

        self.parse_address(address)

        self.add_listener(channel)

        self.data_thread = DataThread(self.ip, self.port, self.module_sn, self.addr,
                                   self.poll)
        self.data_thread.new_data_signal.connect(self.emit_data, Qt.QueuedConnection)
        self.data_thread.start()

        self.metadata_timer = QTimer()
        self.metadata_timer.setInterval(500)
        self.metadata_timer.timeout.connect(self.emit_metadata)
        self.metadata_timer.start()

    def parse_address(self, address):

        data = address.split("/")
        # Parse IP and Port
        data_server = data[0].split(":")
        self.ip = data_server[0]
        self.port = int(data_server[1])

        # Parse slave_id address
        data_data = data[1].split(":")
        if len(data_data) < 2:
            raise ValueError("Invalid Address. The format must be: {}".format(self.ADDRESS_FORMAT))

        self.module_sn = data_data[0]
        self.addr = data_data[1]

        # Check if we have polling defined
        if len(data) > 2:
            self.poll = float(data[2])

    def emit_metadata(self):
        self.emit_access_state()
        self.emit_connection_state(self.data_thread.server.connected and self.data_thread.module is not None )

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
        self.data_thread.server.requestInterruption()
        self.data_thread.requestInterruption()
        self.data_thread.server.disconnect()
#        self.data_thread.terminate()



class ModbusPlugin(PyDMPlugin):
    # NOTE: protocol is intentionally "None" to keep this plugin from getting directly imported.
    # If this plugin is chosen as the One True EPICS Plugin in epics_plugin.py, the protocol will
    # be properly set before it is used.
    protocol = "maq20"
    connection_class = Connection
