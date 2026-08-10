import serial
import struct
import time
import logging
import threading
import json
import datetime
import sys
import ctypes
import os
import re
from ctypes import *

# ===================== 库加载 =====================
try:
    lib = cdll.LoadLibrary("libusbcanfd.so")
except OSError:
    try:
        lib = cdll.LoadLibrary("./libusbcanfd.so")
    except Exception as e:
        print(f"无法加载 libusbcanfd.so: {e}")
        sys.exit(1)

# ===================== 常量与结构体 =====================
USBCANFD = 33
CMD_CAN_TRES = 0x18


# 1. 基础消息结构
class ZCAN_MSG_INFO(Structure):
    _fields_ = [("txm", c_uint, 4), ("fmt", c_uint, 4), ("sdf", c_uint, 1),
                ("sef", c_uint, 1), ("err", c_uint, 1), ("brs", c_uint, 1),
                ("est", c_uint, 1), ("tx", c_uint, 1), ("echo", c_uint, 1),
                ("qsend_100us", c_uint, 1), ("qsend", c_uint, 1), ("pad", c_uint, 15)]


class ZCAN_MSG_HDR(Structure):
    _fields_ = [("ts", c_uint32), ("id", c_uint32), ("inf", ZCAN_MSG_INFO),
                ("pad", c_uint16), ("chn", c_uint8), ("len", c_uint8)]


class ZCAN_20_MSG(Structure):
    _fields_ = [("hdr", ZCAN_MSG_HDR), ("dat", c_ubyte * 8)]


# 2. [修正] 先定义比特率配置结构体
class structure_bit(Structure):
    _fields_ = [("tseg1", c_uint8), ("tseg2", c_uint8), ("sjw", c_uint8),
                ("smp", c_uint8), ("brp", c_uint16)]


# 3. [修正] 再定义引用它的初始化结构体
class ZCANFD_INIT(Structure):
    _fields_ = [("clk", c_uint32), ("mode", c_uint32),
                ("abit", structure_bit), ("dbit", structure_bit)]


class Resistance(Structure):
    _fields_ = [("res", c_uint8)]


# ===================== 全局配置 =====================
class FlushingFileHandler(logging.FileHandler):
    """每次写入日志后强制刷新缓冲区，防止程序崩溃时日志丢失"""
    def emit(self, record):
        super().emit(record)
        self.flush()
# 获取当前运行文件的绝对目录
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# DTC.json 路径：当前目录/DTC/VF5N_DTCLists.json
DTC_JSON_PATH = os.path.join(CURRENT_DIR, "DTC", "VF5555.json")
# DBC.json 路径：当前目录/DBC/VF5Ndbc.json
NODE_DATA_PATH = os.path.join(CURRENT_DIR, "DBC", "dbclist.json")

VOLTAGE_ADJUST_INTERVAL = 60
PORT_NAME = '/dev/ttyUSB0'
CAN_CHANNEL = 0

# 日志文件夹路径：当前目录/logging
LOG_DIR = os.path.join(CURRENT_DIR, "logging")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# 日志文件名：前缀+年月日_时分.log（精确到分钟）
base_filename = os.path.basename(DTC_JSON_PATH)
name_no_ext = os.path.splitext(base_filename)[0]
final_name_prefix = name_no_ext.replace("Lists", "").replace("_List", "").replace("List", "")
# 时间格式改为：年月日_时分（精确到分钟）
current_time_str = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
log_filename = f"{final_name_prefix}_{current_time_str}.log"
# 日志文件完整路径
log_file_path = os.path.join(LOG_DIR, log_filename)

# 配置日志（写入logging文件夹下的分钟级日志文件）
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        FlushingFileHandler(log_file_path, mode='w', encoding='utf-8'),  # 写入指定路径
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# 全局变量
thread_flag = True
print_lock = threading.Lock()
iso_tp_data_cache = {
    0x608: {"total_len": 0, "received_data": [], "is_receiving": False}
}
dtc_mapping_dict = {}
CURRENT_ACTUAL_VOLTAGE = 0.0


# ===================== 自动节点提取逻辑 =====================
def load_simulation_nodes_only(json_path):
    """
    【修改】只加载 VF5Ndbc.json 中的配置，完全忽略 DTC 文件中的节点
    """
    manual_nodes = []
    if not os.path.exists(json_path):
        logger.warning(f"找不到节点配置文件: {json_path}")
        return []

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)

            # 兼容 msgList 格式或直接列表格式
            if isinstance(raw_data, dict) and "msgList" in raw_data:
                node_list = raw_data["msgList"]
            else:
                node_list = raw_data if isinstance(raw_data, list) else [raw_data]

            for item in node_list:
                # 读取配置
                cid = item.get("msgID", 0)
                raw_datas = item.get("datas", [0] * 8)
                data = raw_datas[:8] + [0] * (8 - len(raw_datas[:8])) # 补齐或截断
                cycle_ms = item.get("sendInterval", 100)
                name = item.get("msgName", f"Manual_0x{cid:X}")

                manual_nodes.append({
                    "id": cid,
                    "data": data,
                    "cycle_ms": cycle_ms,
                    "last_send_time": 0,
                    "name": name
                })

        logger.info(f"已加载模拟节点 (仅来源 JSON): {len(manual_nodes)} 条")
        for node in manual_nodes:
             logger.info(f"  节点: ID=0x{node['id']:X} | 周期={node['cycle_ms']}ms | Name={node['name']}")

        return manual_nodes

    except Exception as e:
        logger.error(f"读取节点配置文件失败: {e}")
        return []


# ===================== 电源控制类 =====================
class IT6720FrameController:
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
            self.ser = serial.Serial(port=self.port, baudrate=self.baudrate, timeout=0.5)
            logger.info(f"串口已打开: {self.port}")
            return True
        except Exception as e:
            logger.error(f"串口打开失败: {e}")
            return False

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()

    def _calculate_checksum(self, data):
        return sum(data) & 0xFF

    def _send_frame(self, command, payload=b''):
        if not self.ser or not self.ser.is_open: return
        header = struct.pack('BBB', 0xAA, self.address, command)
        data_part = header + payload
        packet_25 = data_part + (b'\x00' * (25 - len(data_part)))
        final_packet = packet_25 + bytes([self._calculate_checksum(packet_25)])
        self.ser.write(final_packet)
        time.sleep(0.05)

    def  _receive_frame(self):
        if not self.ser: return None
        recv_data = self.ser.read(26)
        if len(recv_data) != 26:
            if self.ser.is_open:
                self.ser.reset_input_buffer()
            return None
        if self._calculate_checksum(recv_data[:25]) != recv_data[25]:
            if self.ser.is_open:
                self.ser.reset_input_buffer()
            return None

        return recv_data

    def set_remote_mode(self, enable=True):
        self._send_frame(self.CMD_REMOTE, bytes([1 if enable else 0]))
        self._receive_frame()

    def set_output(self, enable=True):
        self._send_frame(self.CMD_OUTPUT, bytes([1 if enable else 0]))
        self._receive_frame()

    def set_current(self, current):
        payload = struct.pack('<I', int(current * 1000))
        self._send_frame(self.CMD_SET_CURR, payload)
        self._receive_frame()

    def set_voltage_wait_stable(self, voltage, tolerance=0.01, max_wait_time=30):
        # 【关键修复】引入全局变量，确保这里更新的值能被接收线程看到
        global CURRENT_ACTUAL_VOLTAGE

        payload = struct.pack('<I', int(voltage * 1000))
        self._send_frame(self.CMD_SET_VOLT, payload)
        self._receive_frame()

        start_time = time.time()
        while time.time() - start_time < max_wait_time:
            time.sleep(0.5)  # 稍微加快查询频率
            self._send_frame(self.CMD_READ_STAT)
            response = self._receive_frame()
            if response:
                try:
                    v_mv = struct.unpack('<I', response[5:9])[0]
                    c_ma = struct.unpack('<H', response[3:5])[0]
                    act_v, act_c = v_mv / 1000.0, c_ma / 1000.0

                    # 更新全局变量（现在加了global关键字，生效了）
                    CURRENT_ACTUAL_VOLTAGE = act_v

                    if abs(act_v - voltage) <= tolerance:
                        return act_v, act_c
                except:
                    pass

            if int(time.time() - start_time) % 5 == 0:
                self._send_frame(self.CMD_SET_VOLT, payload)
                self._receive_frame()
        return None, None


# ===================== 业务逻辑函数 =====================
def load_dtc_mapping(json_file_path):
    mapping = {}
    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            root = json.load(f)
            for ecu in root.get("ECU", []):
                for dtc in ecu.get("DTC", []):
                    mapping[dtc["name"].strip()] = dtc["desc"].strip()
        logger.info(f"加载DTC映射表: {len(mapping)}条")
    except Exception as e:
        logger.error(f"加载DTC文件失败: {e}")
    return mapping


def hex_to_dtc_name(value):
    sys_map = {0: 'P', 1: 'C', 2: 'B', 3: 'U'}
    high = (value >> 20) & 0xFF
    if high >> 2 not in sys_map: return None
    return f"{sys_map[high >> 2]}{high & 0x3}{value & 0xFFFFF:05X}"


def node_simulation_thread(dev_type, dev_idx, ch_idx, messages):
    global thread_flag
    logger.info("节点模拟发送线程已启动")
    while thread_flag:
        current_time = time.time() * 1000
        for msg in messages:
            if current_time - msg["last_send_time"] >= msg["cycle_ms"]:
                send_frame_vci(dev_type, dev_idx, ch_idx, msg["id"], msg["data"])
                msg["last_send_time"] = current_time
        time.sleep(0.002)


def parse_uds_response_data(data):
    global thread_flag
    data = list(data)
    if len(data) < 3 or data[0] != 0x59: return

    logger.info(f"解析故障码 (Len={len(data)}):")
    for i in range(3, len(data), 4):
        if i + 3 >= len(data): break
        val = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
        status = data[i + 3]
        dtc_str = hex_to_dtc_name(val) or "Unknown"
        desc = dtc_mapping_dict.get(dtc_str, "未知描述")

        # 过滤 Lose 故障
        if "Lose" in desc and (status & 0x09) != 0:
            pass

        logger.info(f"  {dtc_str}: {desc} (Status: {status:02X})")

        if "ACU内部故障" in desc and "更换ACU" in desc:
            logger.critical("检测到ACU内部故障，程序终止！")
            thread_flag = False

        if "看门狗故障" in desc:
            logger.critical("检测到看门狗故障，程序终止！")
            thread_flag = False



# ===================== CAN VCI 适配层 =====================
def start_can_device(dev_type, dev_idx, ch_idx):
    # 使用正确定义的结构体
    init_cfg = ZCANFD_INIT()
    init_cfg.clk = 60000000
    init_cfg.mode = 0

    # 仲裁域 500k
    init_cfg.abit.tseg1, init_cfg.abit.tseg2 = 14, 3
    init_cfg.abit.sjw, init_cfg.abit.smp, init_cfg.abit.brp = 2, 0, 5

    # 数据域 2M
    init_cfg.dbit.tseg1, init_cfg.dbit.tseg2 = 10, 2
    init_cfg.dbit.sjw, init_cfg.dbit.smp, init_cfg.dbit.brp = 2, 0, 1

    # 新增：打印初始化参数+错误日志
    logger.info(
        f"CAN通道{ch_idx}初始化参数 - 仲裁域(500k) tseg1={init_cfg.abit.tseg1}, tseg2={init_cfg.abit.tseg2}, brp={init_cfg.abit.brp}")
    logger.info(
        f"CAN通道{ch_idx}初始化参数 - 数据域(2M) tseg1={init_cfg.dbit.tseg1}, tseg2={init_cfg.dbit.tseg2}, brp={init_cfg.dbit.brp}")

    if lib.VCI_InitCAN(dev_type, dev_idx, ch_idx, byref(init_cfg)) == 0:
        logger.error(
            f"VCI_InitCAN失败！错误码: {lib.VCI_GetLastError() if hasattr(lib, 'VCI_GetLastError') else '未知'}")
        return False
    logger.info("CAN初始化成功")

    res = Resistance()
    res.res = 1
    lib.VCI_SetReference(dev_type, dev_idx, ch_idx, CMD_CAN_TRES, byref(res))
    logger.info("CAN终端电阻已设置为120Ω")

    if lib.VCI_StartCAN(dev_type, dev_idx, ch_idx) == 0:
        logger.error(
            f"VCI_StartCAN失败！错误码: {lib.VCI_GetLastError() if hasattr(lib, 'VCI_GetLastError') else '未知'}")
        return False
    logger.info("CAN通道启动成功")
    return True

def send_frame_vci(dev_type, dev_idx, ch_idx, can_id, data):
    msg = ZCAN_20_MSG()
    msg.hdr.inf.txm = 0
    msg.hdr.inf.fmt = 0
    msg.hdr.inf.sdf = 0
    msg.hdr.inf.sef = 0
    msg.hdr.id = can_id
    msg.hdr.len = len(data)
    msg.hdr.chn = ch_idx
    for i in range(len(data)): msg.dat[i] = data[i]
    # 新增：检查发送结果并打印日志
    send_ret = lib.VCI_Transmit(dev_type, dev_idx, ch_idx, byref(msg), 1)
    if send_ret == 1:
        logger.info(f"Vol:{CURRENT_ACTUAL_VOLTAGE:.2f}V | TX ID:0x{can_id:X}, data:{[hex(x) for x in data]}")
    else:
        logger.warning(f"发送CAN报文 ❌ - ID:0x{can_id:X}, 返回值:{send_ret}")


def receive_thread_vci(dev_type, dev_idx, ch_idx):
    global thread_flag
    logger.info("CAN接收线程已启动，开始监听实时报文...")  # 新增：确认线程启动
    while thread_flag:
        time.sleep(0.005)
        num = lib.VCI_GetReceiveNum(dev_type, dev_idx, ch_idx)
        if num > 0:
            msgs = (ZCAN_20_MSG * num)()
            cnt = lib.VCI_Receive(dev_type, dev_idx, ch_idx, byref(msgs), num, 50)
            for i in range(cnt):
                frame = msgs[i]
                can_id = frame.hdr.id  # 新增：获取CAN ID
                data_len = frame.hdr.len
                data = list(frame.dat)[:data_len]

                # 核心：实时打印所有接收的CAN报文（控制台+日志）
                real_time = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                with print_lock:  # 线程安全打印
                    logger.info(f"Vol:{CURRENT_ACTUAL_VOLTAGE:.2f}V | RX ID:0x{can_id:X}, data:{[hex(x) for x in data]}")
                # 原有逻辑：处理0x608的ISO-TP故障码报文
                if can_id == 0x608:
                    process_iso_tp(dev_type, dev_idx, ch_idx, data)


def process_iso_tp(dev_type, dev_idx, ch_idx, data):
    if not data: return
    pci = data[0]
    cache = iso_tp_data_cache[0x608]

    if (pci & 0xF0) == 0x10:
        total_len = ((pci & 0x0F) << 8) + data[1]
        cache["total_len"] = total_len
        cache["received_data"] = data[2:]
        cache["is_receiving"] = True
        logger.info(f"ISO-TP 首帧: 总长 {total_len}")
        send_frame_vci(dev_type, dev_idx, ch_idx, 0x688, [0x30, 0, 0, 0, 0, 0, 0, 0])

    elif (pci & 0xF0) == 0x20 and cache["is_receiving"]:
        cache["received_data"].extend(data[1:])
        if len(cache["received_data"]) >= cache["total_len"]:
            full_data = cache["received_data"][:cache["total_len"]]
            parse_uds_response_data(full_data)
            cache["is_receiving"] = False
            cache["received_data"] = []

    elif (pci & 0xF0) == 0x00:
        parse_uds_response_data(data[1:])


# ===================== 主程序 =====================
if __name__ == "__main__":
    dtc_mapping_dict = load_dtc_mapping(DTC_JSON_PATH)
    psu = IT6720FrameController(PORT_NAME)
    recv_thread, sim_thread = None, None
    DEV_IDX = 0

    try:
        # 1. 打开设备
        if lib.VCI_OpenDevice(USBCANFD, DEV_IDX, 0) == 0:
            logger.error("打开CAN设备失败")
            sys.exit(1)

        # 2. 启动CAN
        if not start_can_device(USBCANFD, DEV_IDX, CAN_CHANNEL):
            lib.VCI_CloseDevice(USBCANFD, DEV_IDX)
            sys.exit(1)

        # 3. 接收线程
        recv_thread = threading.Thread(target=receive_thread_vci, args=(USBCANFD, DEV_IDX, CAN_CHANNEL))
        recv_thread.daemon = True
        recv_thread.start()

        # 4. 节点模拟
        sim_node_list = load_simulation_nodes_only(NODE_DATA_PATH)
        if sim_node_list:
            sim_thread = threading.Thread(target=node_simulation_thread,args=(USBCANFD, DEV_IDX, CAN_CHANNEL, sim_node_list))
            sim_thread.daemon = True
            sim_thread.start()
        else:
            logger.warning("未加载到任何模拟节点，请检查 VF5Ndbc.json 文件")

        # 5. 电源连接
        if not psu.connect(): sys.exit(1)
        psu.set_remote_mode(True)
        current_limit = 1.0
        psu.set_current(current_limit)
        psu.set_output(True)

        # 6. 测试循环
        target_voltage = 0.0
        # 上升
        while target_voltage <= 16.0 and thread_flag:
            logger.info(f"=== 目标电压: {target_voltage:.2f}V,设置电流限制: {current_limit:.1f}A ===")
            real_v, real_c = psu.set_voltage_wait_stable(target_voltage)
            if real_v is not None:
                if real_c > 1.0:
                    logger.critical(f"电流超限: {real_c:.3f}A");
                    thread_flag = False;
                    break
                # 倒计时
                logger.info(f" 保持当前电压，剩余等待时间: {VOLTAGE_ADJUST_INTERVAL}秒")
                for remain_sec in range(VOLTAGE_ADJUST_INTERVAL, 0, -1):
                    print(f"\r剩余 {remain_sec} 秒   ", end="", flush=True)

                    # 尝试读取电压更新全局变量
                    try:
                        psu._send_frame(psu.CMD_READ_STAT)
                        resp = psu._receive_frame()
                        if resp:
                            v_mv = struct.unpack('<I', resp[5:9])[0]
                            CURRENT_ACTUAL_VOLTAGE = v_mv / 1000.0
                    except Exception:
                        pass  # 忽略读取错误，保持旧值

                    time.sleep(1)  # 只休眠一次，保证每秒更新一次

                time.sleep(1)
                logger.info("清故障码...")
                send_frame_vci(USBCANFD, DEV_IDX, CAN_CHANNEL, 0x688, [0x04, 0x14, 0xFF, 0xFF, 0xFF, 0, 0, 0])
                time.sleep(5)
                logger.info("读故障码...")
                send_frame_vci(USBCANFD, DEV_IDX, CAN_CHANNEL, 0x688, [0x03, 0x19, 0x02, 0xFF, 0, 0, 0, 0])
                time.sleep(2)
            target_voltage += 0.5

        target_voltage = 15.5
        # 下降
        while target_voltage >= 0.0 and thread_flag:
            logger.info(f"=== 目标电压: {target_voltage:.2f}V,设置电流限制: {current_limit:.1f}A ===")
            real_v, real_c = psu.set_voltage_wait_stable(target_voltage)
            if real_v is not None:
                if real_c > 1.0:
                    logger.critical(f"电流超限: {real_c:.3f}A");
                    thread_flag = False;
                    break

                # 倒计时
                logger.info(f" 保持当前电压，剩余等待时间: {VOLTAGE_ADJUST_INTERVAL}秒")
                for remain_sec in range(VOLTAGE_ADJUST_INTERVAL, 0, -1):
                    print(f"\r剩余 {remain_sec} 秒   ", end="", flush=True)

                    # 尝试读取电压更新全局变量
                    try:
                        psu._send_frame(psu.CMD_READ_STAT)
                        resp = psu._receive_frame()
                        if resp:
                            v_mv = struct.unpack('<I', resp[5:9])[0]
                            CURRENT_ACTUAL_VOLTAGE = v_mv / 1000.0
                    except Exception:
                        pass  # 忽略读取错误，保持旧值

                    time.sleep(1)  # 只休眠一次，保证每秒更新一次
                logger.info("清故障码...")
                send_frame_vci(USBCANFD, DEV_IDX, CAN_CHANNEL, 0x688, [0x04, 0x14, 0xFF, 0xFF, 0xFF, 0, 0, 0])
                time.sleep(5)
                logger.info("读故障码...")
                send_frame_vci(USBCANFD, DEV_IDX, CAN_CHANNEL, 0x688, [0x03, 0x19, 0x02, 0xFF, 0, 0, 0, 0])
                time.sleep(2)
            target_voltage -= 0.5

        if thread_flag: logger.info("测试完成")

    except KeyboardInterrupt:
        logger.info("用户中断")
    except Exception as e:
        logger.error(f"异常: {e}", exc_info=True)
    finally:
        thread_flag = False
        if recv_thread: recv_thread.join(timeout=1)
        if sim_thread: sim_thread.join(timeout=1)
        psu.set_output(False)
        psu.close()
        lib.VCI_CloseDevice(USBCANFD, DEV_IDX)
        logging.shutdown()