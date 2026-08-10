import serial
import struct
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


class IT6720PowerSupply:
    CMD_REMOTE, CMD_OUTPUT = 0x20, 0x21
    CMD_SET_VOLT, CMD_SET_CURR = 0x23, 0x24
    CMD_READ_STAT = 0x26

    def __init__(self, port, baudrate=4800, address=0x01):
        self.port = port
        self.baudrate = baudrate
        self.address = address
        self.ser = None

    def connect(self):
        try:
            # timeout设置得短一点，配合高速轮询
            self.ser = serial.Serial(port=self.port, baudrate=self.baudrate, timeout=0.2)
            return True
        except Exception as e:
            logger.error(f"打开失败: {e}")
            return False

    def close(self):
        if self.ser and self.ser.is_open:
            self.set_output(False)
            self.set_remote_mode(False)
            self.ser.close()

    def _calculate_checksum(self, data):
        return sum(data) & 0xFF

    def _send_frame(self, command, payload=b''):
        if not self.ser or not self.ser.is_open: return
        self.ser.reset_input_buffer()
        header = struct.pack('BBB', 0xAA, self.address, command)
        data_part = header + payload
        packet_25 = data_part + (b'\x00' * (25 - len(data_part)))
        final_packet = packet_25 + bytes([self._calculate_checksum(packet_25)])

        self.ser.write(final_packet)
        self.ser.flush()  # 确保数据物理发出，去除了原来的 time.sleep 延时

    def _receive_frame(self):
        if not self.ser: return None
        sync_byte = self.ser.read(1)
        while sync_byte and sync_byte != b'\xAA':
            sync_byte = self.ser.read(1)
        if not sync_byte: return None
        rest_data = self.ser.read(25)
        if len(rest_data) != 25: return None
        full_frame = b'\xAA' + rest_data
        if self._calculate_checksum(full_frame[:25]) != full_frame[25]: return None
        return full_frame

    def set_remote_mode(self, enable=True):
        self._send_frame(self.CMD_REMOTE, bytes([1 if enable else 0]))
        self._receive_frame()

    def set_output(self, enable=True):
        self._send_frame(self.CMD_OUTPUT, bytes([1 if enable else 0]))
        self._receive_frame()

    def set_current(self, current_A):
        payload = struct.pack('<I', int(current_A * 1000))
        self._send_frame(self.CMD_SET_CURR, payload)
        self._receive_frame()

    def set_voltage(self, voltage_V):
        payload = struct.pack('<I', int(voltage_V * 1000))
        self._send_frame(self.CMD_SET_VOLT, payload)
        self._receive_frame()

    def read_status(self):
        self._send_frame(self.CMD_READ_STAT)
        response = self._receive_frame()
        if response:
            try:
                v_mv = struct.unpack('<I', response[5:9])[0]
                c_ma = struct.unpack('<H', response[3:5])[0]
                return v_mv / 1000.0, c_ma / 1000.0
            except:
                pass
        return None, None

    def set_voltage_wait_stable(self, voltage_V, tolerance=0.02, max_wait_time=30):
        # 1. 发送设置电压指令，并立刻记录起始时间
        self.set_voltage(voltage_V)
        start_time = time.time()

        last_retry_time = start_time
        while time.time() - start_time < max_wait_time:

            # 【核心修改】：去掉了这里的 time.sleep，让程序疯狂读取状态
            act_v, act_c = self.read_status()

            if act_v is not None:
                current_elapsed = time.time() - start_time
                print(f"  [监测] {current_elapsed:.3f}秒 --> 实际电压: {act_v:.3f} V")

                if abs(act_v - voltage_V) <= tolerance:
                    return act_v, act_c, current_elapsed

            if time.time() - last_retry_time > 5:
                self.set_voltage(voltage_V)
                last_retry_time = time.time()

        return None, None, None


if __name__ == "__main__":
    PORT = 'COM1'  # 你的实际端口
    psu = IT6720PowerSupply(port=PORT)

    if psu.connect():
        try:
            psu.set_remote_mode(True)
            psu.set_current(2.0)
            psu.set_output(True)

            print(">>> 正在将电压归零，准备测试 ...")
            psu.set_voltage_wait_stable(0.0)
            time.sleep(1)  # 给电源内部电容留1秒放电时间，保证从0开始

            print("\n>>> 🚀 极速轮询模式开启：从 0V 跃变到 12V")
            real_v, real_c, time_cost = psu.set_voltage_wait_stable(12.0)

            if real_v is not None:
                print("\n========================================")
                print(f"🎯 达标！最终稳定电压: {real_v:.3f} V")
                print(f"⏱️ 物理通信+电源内部爬升 总耗时: {time_cost:.3f} 秒")
                print("========================================")
            else:
                print("等待电压稳定超时！")

        except KeyboardInterrupt:
            pass
        finally:
            psu.close()