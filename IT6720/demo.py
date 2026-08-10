import time
import logging
import threading
import serial
import struct
from datetime import datetime
from zlgcan import *

# ==================== 日志配置 ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s.%(msecs)03d - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)


# ==================== 电源控制类 ====================
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
            self.ser = serial.Serial(port=self.port, baudrate=self.baudrate, timeout=0.2)
            return True
        except Exception as e:
            logger.error(f"电源串口打开失败: {e}")
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
        self.ser.flush()

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
        # 这里不等待响应，直接返回，让调用者决定是否等待稳定

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

    def set_voltage_with_monitoring(self, target_voltage, monitoring_callback=None, step_interval=0.05,
                                    max_wait_time=30):
        """
        设置电压并持续监控电压变化过程
        :param target_voltage: 目标电压
        :param monitoring_callback: 监控回调函数，接收(时间戳, 电压值, 电流值)参数
        :param step_interval: 监控步进间隔
        :param max_wait_time: 最大等待时间
        """
        # 发送目标电压设置命令
        self.set_voltage(target_voltage)

        start_time = time.perf_counter()
        last_voltage = None

        while time.perf_counter() - start_time < max_wait_time:
            act_v, act_c = self.read_status()

            if act_v is not None:
                # 记录当前电压值
                current_time = time.time()

                # 如果回调函数存在，执行回调
                if monitoring_callback:
                    monitoring_callback(current_time, act_v, act_c)

                # 更新最后电压值
                last_voltage = act_v

                # 检查是否达到稳定状态
                if abs(act_v - target_voltage) <= 0.02:
                    elapsed_time = time.perf_counter() - start_time
                    return act_v, act_c, elapsed_time

            # 小间隔等待，提高监测频率
            time.sleep(step_interval)

        # 超时返回
        elapsed_time = time.perf_counter() - start_time
        return last_voltage, None if last_voltage is None else self.read_status()[1], elapsed_time


# ==================== CAN 通信类 ====================
class ZlgUsbcandFd200U:
    def __init__(self, channel=0):
        self.zcanlib = ZCAN()
        self.dev_handle = INVALID_DEVICE_HANDLE
        self.chn_handle = INVALID_CHANNEL_HANDLE
        self.channel = channel

        self.is_running = False
        self.recv_thread = None
        self.data_lock = threading.Lock()

        # 抓取测试专用的变量
        self.is_capturing = False
        self.first_msg = None
        self.last_msg = None

    def connect(self, abit_baud="500000", dbit_baud="2000000", enable_resistor=True):
        self.dev_handle = self.zcanlib.OpenDevice(ZCAN_USBCANFD_200U, 0, 0)
        if self.dev_handle == INVALID_DEVICE_HANDLE: return False

        try:
            self.zcanlib.ZCAN_SetValue(self.dev_handle, f"{self.channel}/canfd_abit_baud_rate",
                                       abit_baud.encode("utf-8"))
            self.zcanlib.ZCAN_SetValue(self.dev_handle, f"{self.channel}/canfd_dbit_baud_rate",
                                       dbit_baud.encode("utf-8"))
            res_val = "1" if enable_resistor else "0"
            self.zcanlib.ZCAN_SetValue(self.dev_handle, f"{self.channel}/initenal_resistance", res_val.encode("utf-8"))
            self.zcanlib.ZCAN_SetValue(self.dev_handle, f"{self.channel}/set_device_tx_echo", b"0")
        except Exception as e:
            self.close()
            return False

        chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
        chn_init_cfg.can_type = ZCAN_TYPE_CANFD
        chn_init_cfg.config.canfd.mode = 0

        self.chn_handle = self.zcanlib.InitCAN(self.dev_handle, self.channel, chn_init_cfg)
        if self.chn_handle is None or self.chn_handle == INVALID_CHANNEL_HANDLE: return False

        if self.zcanlib.StartCAN(self.chn_handle) != ZCAN_STATUS_OK: return False
        self.zcanlib.ClearBuffer(self.chn_handle)
        return True

    def start_receive_thread(self):
        if not self.is_running:
            self.is_running = True
            self.recv_thread = threading.Thread(target=self._receive_task)
            self.recv_thread.daemon = True
            self.recv_thread.start()

    def _receive_task(self):
        while self.is_running:
            time.sleep(0.002)  # 高速轮询释放CPU

            # 1. 经典CAN
            rcv_num = self.zcanlib.GetReceiveNum(self.chn_handle, ZCAN_TYPE_CAN)
            if rcv_num > 0:
                rcv_msgs, rcv_num = self.zcanlib.Receive(self.chn_handle, min(rcv_num, 100), 50)
                for msg in rcv_msgs[:rcv_num]:
                    self._process_frame(msg.frame, is_fd=False)

            # 2. CANFD
            rcv_canfd_num = self.zcanlib.GetReceiveNum(self.chn_handle, ZCAN_TYPE_CANFD)
            if rcv_canfd_num > 0:
                rcv_canfd_msgs, rcv_canfd_num = self.zcanlib.ReceiveFD(self.chn_handle, min(rcv_canfd_num, 100), 50)
                for msg in rcv_canfd_msgs[:rcv_canfd_num]:
                    self._process_frame(msg.frame, is_fd=True)

    def _process_frame(self, frame, is_fd):
        """解析、打印并记录报文"""
        can_id = frame.can_id & 0x1FFFFFFF
        frame_type = "扩展帧" if frame.can_id & (1 << 31) else "标准帧"

        if is_fd:
            protocol = f"CANFD"
            dlc = frame.len
        else:
            protocol = "经典CAN"
            dlc = frame.can_dlc

        data_str = " ".join([f"{num:02X}" for num in frame.data[:dlc]])
        parsed_info = f"{protocol} | {frame_type} | ID: 0x{can_id:03X} | DLC: {dlc:02d} | Data: {data_str}"
        timestamp = time.time()

        with self.data_lock:
            # 💡 【修改点】：解开注释，实时打印所有收到的报文让你直接查看
            logger.info(f"RX: {parsed_info}")

            if self.is_capturing:
                # 记录第一帧
                if self.first_msg is None:
                    self.first_msg = (timestamp, parsed_info)
                    logger.info(f"========== [触发] 抓取到上电后的 第一帧 !! ==========")

                # 不断覆盖，保留最后一次接收的帧
                self.last_msg = (timestamp, parsed_info)

    def clear_capture(self):
        """清空抓取记录"""
        with self.data_lock:
            self.first_msg = None
            self.last_msg = None

    def close(self):
        self.is_running = False
        if self.recv_thread:
            self.recv_thread.join(timeout=1)
        if self.dev_handle != INVALID_DEVICE_HANDLE:
            self.zcanlib.CloseDevice(self.dev_handle)
            self.dev_handle = INVALID_DEVICE_HANDLE


# ==================== 主测试流程 ====================
def run_acu_power_test(psu_port, can_channel=0):
    psu = IT6720PowerSupply(port=psu_port)
    can = ZlgUsbcandFd200U(channel=can_channel)

    # 用于存储电压监测数据的列表
    voltage_log = []

    def voltage_monitor_callback(timestamp, voltage, current):
        """电压监测回调函数"""
        now_str = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S.%f")[:-3]
        log_entry = f"[监测] {now_str} --> 实际电压: {voltage:.3f} V, 电流: {current:.3f} A"
        print(f"  {log_entry}")
        voltage_log.append((timestamp, voltage, current))

    # 1. 建立设备连接
    logger.info(">>> 1. 正在连接设备...")
    if not psu.connect(): return
    if not can.connect(abit_baud="500000", dbit_baud="2000000"): return
    logger.info(">>> 设备连接成功！")

    try:
        # 2. 电源初始化：归零、打开输出
        psu.set_remote_mode(True)
        psu.set_current(2.0)  # 限流2A，根据你的ACU调整
        psu.set_output(True)
        logger.info(">>> 2. 正在将电源归零...")
        psu.set_voltage_with_monitoring(0.0, voltage_monitor_callback)
        time.sleep(1)  # 等待内部电容放电，确保ACU完全断电

        # 3. 启动CAN接收，准备上电
        can.start_receive_thread()
        can.zcanlib.ClearBuffer(can.chn_handle)  # 清空硬件缓存
        can.clear_capture()
        can.is_capturing = True  # 开启抓取模式

        time_power_on_cmd = time.time()
        logger.info("\n================================================")
        logger.info(">>> 3. 极速上电：0V -> 12V，等待 ACU 启动并发报文...")

        # 使用新的带监控的电压设置方法
        real_v, _, duration = psu.set_voltage_with_monitoring(12.0, voltage_monitor_callback, step_interval=0.01)

        if real_v:
            logger.info(f"电源已到达: {real_v:.2f}V，请观察下方报文...")

        # 给予ACU一定的工作时间 (持续运行5秒，在此期间你会看到报文疯狂刷屏)
        time.sleep(5)

        # 4. 开始下电流程
        logger.info("\n================================================")
        logger.info(">>> 4. 开始下电：12V -> 0V，等待 ACU 停止发报文...")

        real_v, _, duration = psu.set_voltage_with_monitoring(0.0, voltage_monitor_callback, step_interval=0.02)

        if real_v is not None:
            logger.info(f"⚡ 电源已降至: {real_v:.2f}V")

        # 此时ACU内部电容可能还有余电，会继续发一会儿报文
        logger.info("等待 3 秒确保 ACU 电容放完、报文完全停止...")
        time.sleep(3)

        # 5. 停止抓取
        can.is_capturing = False

        # ==================== 最终结果输出 ====================
        logger.info("\n" + "*" * 60)
        logger.info("测试结果汇总报告")
        logger.info("*" * 60)

        if can.first_msg:
            t_first, data_first = can.first_msg
            delay_ms = (t_first - time_power_on_cmd) * 1000
            logger.info(f"🟢 【上电第一帧】")
            logger.info(f"   - 收到时间: 距离发上电指令 {delay_ms:.2f} 毫秒")
            logger.info(f"   - 报文内容: {data_first}")
        else:
            logger.warning("❌ 未抓取到任何上电报文 (ACU可能未启动或波特率错误)")

        if can.last_msg:
            t_last, data_last = can.last_msg
            logger.info(f"🔴 【下电最后一帧】")
            logger.info(
                f"   - 收到时间: {time.strftime('%H:%M:%S', time.localtime(t_last))}.{int((t_last % 1) * 1000):03d}")
            logger.info(f"   - 报文内容: {data_last}")

            # 若第一帧和最后一帧都有，可以计算ACU本次存活发报文的总时长
            if can.first_msg:
                alive_time = t_last - can.first_msg[0]
                logger.info(f"⏱️ 【ACU通讯存活时间】: {alive_time:.3f} 秒")
        else:
            logger.warning("❌ 未抓取到最后帧记录")

        # 输出电压变化日志摘要
        logger.info(f"\n📊 【电压监测数据】共记录 {len(voltage_log)} 个电压采样点")
        if voltage_log:
            start_voltage = voltage_log[0][1]
            end_voltage = voltage_log[-1][1]
            logger.info(f"   - 起始电压: {start_voltage:.3f} V")
            logger.info(f"   - 结束电压: {end_voltage:.3f} V")

        logger.info("*" * 60 + "\n")

    except KeyboardInterrupt:
        logger.info("人为中断测试...")
    finally:
        logger.info(">>> 正在清理资源并关闭设备...")
        psu.close()
        can.close()
        logger.info(">>> 测试结束。")


if __name__ == "__main__":
    # 配置你的电源串口号和CAN通道
    POWER_PORT = 'COM1'  # <--- 请在此处修改为实际的串口号，例如 'COM3'
    CAN_CHANNEL = 0

    run_acu_power_test(psu_port=POWER_PORT, can_channel=CAN_CHANNEL)
