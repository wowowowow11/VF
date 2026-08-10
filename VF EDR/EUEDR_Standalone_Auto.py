import json
import random
import datetime
import time
import threading
import os
import sys
import ctypes  # 导入 ctypes 用于安全算法库加载
import queue   # 导入 queue 用于高性能异步日志处理
from typing import Dict, List, Any

# 尝试引入 yaml，若未安装则引导用户
try:
    import yaml
except ImportError:
    print("错误: 缺少 yaml 库，请使用 'pip install pyyaml' 进行安装。")
    raise

# 导入 zlgcan 驱动库中的必要定义
from zlgcan import (
    ZCAN,
    ZCAN_Transmit_Data,
    ZCAN_CHANNEL_INIT_CONFIG,
    ZCAN_STATUS_OK,
    ZCAN_USBCANFD_200U,
)

# ==================== 硬件及通道配置 ====================
DEVICE_TYPE = ZCAN_USBCANFD_200U
DEVICE_INDEX = 0
CHANNEL_INDEX = 0
BAUD_RATE = "500000"  # 波特率 500k

# ==================== 诊断寻址配置 ====================
DiagnosticAddressing = {
    "EDRReqAddressing": 0x688,  # 请求 ID
    "EDRResAddressing": 0x608  # 响应 ID
}

paramDataLength = 898  # VF6N_EDR 原始数据长度
proName = "VF65"
stop_periodic = False  # 周期发送控制标志

# 全局日志文件路径，用于记录所有测试过程以供 outputReport 转换 HTML
log_file_path = None

# 建立 Logs 文件夹，定义报文日志路径
LOG_DIR = "Logs"
os.makedirs(LOG_DIR, exist_ok=True)
traffic_log_filename = os.path.join(LOG_DIR, f"CAN_Bus_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# 控制清除序列详细日志打印标志位
_mute_diag_logs = False

# 后台周期发送的数据字典：key为CAN_ID (int)，value为{"data": list, "ms": int, "next_send": float}
periodic_messages = {}
periodic_lock = threading.Lock()

# ACU 前置条件消息
acu_preconditions = [
    {"id": 0x20D, "data": [0x0A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], "ms": 20},
    {"id": 0x112, "data": [0x2D, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00], "ms": 100},
    {"id": 0x40D, "data": [0x17, 0x00, 0x00, 0x00, 0x00, 0x07, 0xD0, 0x00], "ms": 120},
]

# 完整的 VF6N_EDR 原始解析配置 JSON 字符串
jsonData = """
{
    "01Delta-V_longitudinal": {
        "Description": "Delta-V_longitudinal",
        "Factor": 1,
        "Offset": -127,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 26
    },
    "02Maximum delta-V_longitudinal": {
        "Description": "Maximum delta-V_longitudinal",
        "Factor": 1,
        "Offset": -127,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "03Time_maximum delta-V_longitudinal": {
        "Description": "Time_maximum delta-V_longitudinal",
        "Factor": 2.5,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "04Speed_vehicle indicated": {
        "Description": "Speed_vehicle indicated",
        "Factor": 0.05625,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 2,
        "Length": 22
    },
    "05Engine throttle_percent full (accelerator pedal percent full)": {
        "Description": "Engine throttle_percent full (accelerator pedal percent full)",
        "Factor": 0.0625,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 2,
        "Length": 22
    },
    "06Service brake_on/off": {
        "Description": "Service brake_on/off",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "07Ignition cycle_crash": {
        "Description": "Ignition cycle_crash",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 2,
        "Length": 2
    },
    "08Ignition cycle_download": {
        "Description": "Ignition cycle_download",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 2,
        "Length": 2
    },
    "09Safety belt status_driver": {
        "Description": "Safety belt status_driver",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "10Air bag warning lamp": {
        "Description": "Air bag warning lamp",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "11Frontal air bag deployment_time to deploy/first stage_driver": {
        "Description": "Frontal air bag deployment_time to deploy/first stage_driver",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "12Frontal air bag deployment_time to deploy/first stage_right front passenger": {
        "Description": "Frontal air bag deployment_time to deploy/first stage_right front passenger",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "13Multi-event_number of event": {
        "Description": "Multi-event_number of event",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "14Time from event 1 to 2": {
        "Description": "Time from event 1 to 2",
        "Factor": 0.1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "15Complete file recorded": {
        "Description": "Complete file recorded",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "16Lateral acceleration (post-crash)": {
        "Description": "Lateral acceleration (post-crash)",
        "Factor": 1,
        "Offset": -127,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 126
    },
    "17Longitudinal acceleration (post-crash)": {
        "Description": "Longitudinal acceleration (post-crash)",
        "Factor": 1,
        "Offset": -127,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 126
    },
    "18Normal Acceleration (post-crash)": {
        "Description": "Normal Acceleration (post-crash)",
        "Factor": 0.5,
        "Offset": -63.5,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 61
    },
    "19Delta-V_lateral": {
        "Description": "Delta-V_lateral",
        "Factor": 1,
        "Offset": -127,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 26
    },
    "20Maximum delta-V_lateral": {
        "Description": "Maximum delta-V_lateral",
        "Factor": 1,
        "Offset": -127,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "21Time_maximum delta-V_lateral": {
        "Description": "Time_maximum delta-V_lateral",
        "Factor": 2.5,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "22Time_maximum delta-V_resultant": {
        "Description": "Time_maximum delta-V_resultant",
        "Factor": 2.5,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "23Engine rpm_front EDS": {
        "Description": "Engine rpm_front EDS",
        "Factor": 1,
        "Offset": -32767,
        "Data": [],
        "SingleSignalLength": 2,
        "Length": 22
    },
    "24Vehicle Roll Angle": {
        "Description": "Vehicle Roll Angle",
        "Factor": 10,
        "Offset": -1270,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 61
    },
    "25Vehicle roll rate": {
        "Description": "Vehicle roll rate",
        "Factor": 1,
        "Offset": -32767,
        "Data": [],
        "SingleSignalLength": 2,
        "Length": 122
    },
    "26Anti-lock braking system activity": {
        "Description": "Anti-lock braking system activity",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "27Stability control": {
        "Description": "Stability control",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "28Steering input": {
        "Description": "Steering input",
        "Factor": 0.0238,
        "Offset": -780,
        "Data": [],
        "SingleSignalLength": 2,
        "Length": 22
    },
    "29Safety belt status_right front passenger": {
        "Description": "Safety belt status_right front passenger",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "30Frontal air bag suppression switch status_right front passenger": {
        "Description": "Frontal air bag suppression switch status_right front passenger",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "31Frontal air bag deployment_time to 2nd stage_driver": {
        "Description": "Frontal air bag deployment_time to 2nd stage_driver",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "32Frontal air bag deployment_time to 2nd stage_right front passenger": {
        "Description": "Frontal air bag deployment_time to 2nd stage_right front passenger",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "33Far side impact center airbag_time to deploy": {
        "Description": "Far side impact center airbag_time to deploy",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "34Side air bag deployment_time to deploy_driver": {
        "Description": "Side air bag deployment_time to deploy_driver",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "35Side air bag deployment_time to deploy_right front passenger": {
        "Description": "Side air bag deployment_time to deploy_right front passenger",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "36Side air bag deployment_time to deploy_2nd row driver side": {
        "Description": "Side air bag deployment_time to deploy_2nd row driver side",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "37Side air bag deployment_time to deploy_2nd row passenger side": {
        "Description": "Side air bag deployment_time to deploy_2nd row passenger side",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "38Side curtain/tube air bag deployment_time to deploy_driver side": {
        "Description": "Side curtain/tube air bag deployment_time to deploy_driver side",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "39Side curtain/tube air bag deployment_time to deploy_passenger side": {
        "Description": "Side curtain/tube air bag deployment_time to deploy_passenger side",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "40Frontal air bag deployment_2nd stage disposal_driver": {
        "Description": "Frontal air bag deployment_2nd stage disposal_driver",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "41Frontal air bag deployment_2nd stage disposal_right front passenger": {
        "Description": "Frontal air bag deployment_2nd stage disposal_right front passenger",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "42Knee air bag deployment_time to deploy_driver": {
        "Description": "Knee air bag deployment_time to deploy_driver",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "43Knee air bag deployment_time to deploy_passener": {
        "Description": "Knee air bag deployment_time to deploy_passener",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "44Pretensioner deployment_time to fire 1st stage_driver": {
        "Description": "Pretensioner deployment_time to fire 1st stage_driver",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "45Pretensioner deployment_time to fire 2nd stage_driver": {
        "Description": "Pretensioner deployment_time to fire 2nd stage_driver",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "46Pretensioner deployment_time to fire 1st stage_right front passenger": {
        "Description": "Pretensioner deployment_time to fire 1st stage_right front passenger",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "47Pretensioner deployment_time to fire 2nd stage_right front passenger": {
        "Description": "Pretensioner deployment_time to fire 2nd stage_right front passenger",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "48Belt Anchor deployment_time to fire _driver": {
        "Description": "Belt Anchor deployment_time to fire _driver",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "49Belt Anchor deployment_time to fire _right passenger": {
        "Description": "Belt Anchor deployment_time to fire _right passenger",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "50Seat track position switch_foremost_status_driver": {
        "Description": "Seat track position switch_foremost_status_driver",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "51Seat track position switch_foremost_status_right front passenger": {
        "Description": "Seat track position switch_foremost_status_right front passenger",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "52Occupant size classification_right front passenger": {
        "Description": "Occupant size classification_right front passenger",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "53Safety belt status_2nd row left side": {
        "Description": "Safety belt status_2nd row left side",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "54Safety belt status_2nd row middle": {
        "Description": "Safety belt status_2nd row middle",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "55Safety belt status_2nd row right side": {
        "Description": "Safety belt status_2nd row right side",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "56Tyre Pressure Monitoring System (TPMS) Warning Lamp Status": {
        "Description": "Tyre Pressure Monitoring System (TPMS) Warning Lamp Status",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "57Longitudinal acceleration (pre - crash)": {
        "Description": "Longitudinal acceleration (pre - crash)",
        "Factor": 0.1,
        "Offset": -12.7,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "58Lateral acceleration (pre - crash)": {
        "Description": "Lateral acceleration (pre - crash)",
        "Factor": 0.1,
        "Offset": -12.7,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "59Yaw Rate": {
        "Description": "Yaw Rate",
        "Factor": 0.1,
        "Offset": -32767,
        "Data": [],
        "SingleSignalLength": 2,
        "Length": 22
    },
    "60Traction Control Status": {
        "Description": "Traction Control Status",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "61Advanced emergency braking system status": {
        "Description": "Advanced emergency braking system status",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "62Cruise Control System": {
        "Description": "Cruise Control System",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "63Adaptive Cruise Control Status (driving automation system level 1)": {
        "Description": "Adaptive Cruise Control Status (driving automation system level 1)",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "64HV battery disconnect_time to deploy": {
        "Description": "HV battery disconnect_time to deploy",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "65Lane departure warning system status": {
        "Description": "Lane departure warning system status",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "66Corrective steering function status": {
        "Description": "Corrective steering function status",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "67Emergency steering function status": {
        "Description": "Emergency steering function status",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "68Automatically commanded steering function category B1 status": {
        "Description": "Automatically commanded steering function category B1 status",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "69Automatically commanded steering function category C status": {
        "Description": "Automatically commanded steering function category C status",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "70Accident emergency call system status": {
        "Description": "Accident emergency call system status",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 11
    },
    "71Accident time - Year": {
        "Description": "Accident time - Year",
        "Factor": 1,
        "Offset": 2000,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "72Accident time - Month": {
        "Description": "Accident time - Month",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "73Accident time - Day": {
        "Description": "Accident time - Day",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "74Accident time - Hour": {
        "Description": "Accident time - Hour",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "75Accident time - Minute": {
        "Description": "Accident time - Minute",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "76Accident time - Second": {
        "Description": "Accident time - Second",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    },
    "77Vehicle Mileage": {
        "Description": "Vehicle Mileage",
        "Factor": 1,
        "Offset": 0,
        "Data": [],
        "SingleSignalLength": 3,
        "Length": 3
    },
    "78Vehicle Identification Number (VIN)": {
        "Description": "Vehicle Identification Number (VIN)",
        "Data": [],
        "Type": "ASCII",
        "SingleSignalLength": 1,
        "Length": 7
    },
    "79CRC Checksum CRC32": {
        "Description": "CRC Checksum CRC32",
        "Data": [],
        "SingleSignalLength": 1,
        "Length": 1
    }
}
"""


# ==================== 日志核心模块 ====================

def write_log(msg, force=False):
    """
    修改后的写日志方法：支持向终端打印的同时，
    向全局定义好的 Logs/ 目录下实时写入追加测试 log 文件。
    如果 _mute_diag_logs 为 True 且 force 为 False，将静默，不输出任何详细流程步骤。
    """
    global _mute_diag_logs
    if _mute_diag_logs and not force:
        return
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    log_line = f"[{timestamp}] {msg}"
    print(log_line)
    if log_file_path:
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(log_line + "\n")
        except Exception:
            pass


# ==================== 异步总线报文日志记录器 ====================

traffic_counter = 0
traffic_lock = threading.Lock()
traffic_queue = queue.Queue()


def init_traffic_log():
    """初始化总线报文流日志，写入表头"""
    header = f"{'序号':<8}{'时间标识':<15}{'源通道':<8}{'帧ID':<10}{'帧类型':<10}{'帧格式':<10}{'CAN类型':<10}{'方向':<8}{'长度':<6}{'数据'}"
    with open(traffic_log_filename, "w", encoding="utf-8") as f:
        f.write(header + "\n")


def log_bus_traffic(direction, can_id, dlc, data, can_type="CAN", channel=0):
    """提取单帧数据，格式化对齐后推入缓冲队列"""
    global traffic_counter
    with traffic_lock:
        is_extended = bool(can_id & 0x80000000)
        is_remote = bool(can_id & 0x40000000)

        frame_id_val = can_id & 0x1FFFFFFF
        frame_type_str = "扩展帧" if is_extended else "标准帧"
        frame_format_str = "远程帧" if is_remote else "数据帧"

        timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        id_hex = f"0x{frame_id_val:X}"
        data_hex = " ".join([f"{b:02X}" for b in data[:dlc]])

        row = f"{traffic_counter:<8}{timestamp:<15}{channel:<8}{id_hex:<10}{frame_type_str:<10}{frame_format_str:<10}{can_type:<10}{direction:<8}{dlc:<6}{data_hex}"
        traffic_queue.put(row)
        traffic_counter += 1


def traffic_writer_thread_func():
    """磁盘写入消费者线程 (缓冲防卡顿)"""
    global thread_flag
    with open(traffic_log_filename, "a", encoding="utf-8", buffering=8192) as f:
        while thread_flag or not traffic_queue.empty():
            try:
                row = traffic_queue.get(timeout=0.1)
                f.write(row + "\n")
            except queue.Empty:
                continue


# ==================== 底层 CAN 发送与周期线程 ====================

def send_raw_can(can_dll, chn_handle, can_id, data):
    transmit_obj = ZCAN_Transmit_Data()
    transmit_obj.transmit_type = 0  # 正常发送
    transmit_obj.frame.can_id = can_id
    transmit_obj.frame.can_dlc = len(data)
    for idx, val in enumerate(data):
        transmit_obj.frame.data[idx] = val

    # 全局捕获并记录所有 Tx 报文（包括周期 Node 发送和诊断 Tx）
    log_bus_traffic("Tx", can_id, len(data), data, can_type="CAN", channel=CHANNEL_INDEX)

    ret = can_dll.Transmit(chn_handle, transmit_obj, 1)
    return ret == ZCAN_STATUS_OK


def periodic_sender_loop(can_dll, chn_handle):
    global stop_periodic
    while not stop_periodic:
        now = time.time()
        with periodic_lock:
            for can_id, msg in list(periodic_messages.items()):
                if now >= msg["next_send"]:
                    send_raw_can(can_dll, chn_handle, msg["id"] if "id" in msg else can_id, msg["data"])
                    msg["next_send"] = now + (msg["ms"] / 1000.0)
        time.sleep(0.001)


# ==================== ISO 15765-2 (CAN-TP) 协议发送与接收 ====================

def tp_transmit(can_dll, chn_handle, req_id, res_id, payload, timeout_ms=2000) -> bool:
    """
    支持单帧和多帧（CAN-TP）发送。
    当 payload 大于 7 字节时，自动拆包为首帧（FF）和连续帧（CF），并等待流控制帧（FC）。
    """
    total_len = len(payload)
    if total_len <= 7:
        # 单帧发送 (Single Frame)
        sf_frame = [total_len] + payload
        while len(sf_frame) < 8:
            sf_frame.append(0x00)
        return send_raw_can(can_dll, chn_handle, req_id, sf_frame)
    else:
        # 多帧发送 - 首帧 (First Frame)
        ff_pci0 = 0x10 | ((total_len >> 8) & 0x0F)
        ff_pci1 = total_len & 0xFF
        ff_frame = [ff_pci0, ff_pci1] + payload[:6]

        # 清除缓冲区，准备接收流控制帧 (FC)
        can_dll.ClearBuffer(chn_handle)

        if not send_raw_can(can_dll, chn_handle, req_id, ff_frame):
            write_log("Failed to send First Frame.")
            return False

        # 等待流控制帧 (Flow Control CTS)
        fc_received = False
        st_min_ms = 10  # 默认延时
        start_time = time.time()

        while (time.time() - start_time) < (timeout_ms / 1000.0):
            num = can_dll.GetReceiveNum(chn_handle, 0)
            if num > 0:
                msgs, ret_num = can_dll.Receive(chn_handle, num, 50)
                for i in range(ret_num):
                    msg = msgs[i].frame
                    if msg.can_id == res_id:
                        frame_data = list(msg.data[:msg.can_dlc])
                        if frame_data and (frame_data[0] & 0xF0) == 0x30:  # Flow Control Frame
                            # 解析 STmin 发送间隔
                            st_min_val = frame_data[2]
                            if st_min_val <= 0x7F:
                                st_min_ms = st_min_val
                            elif 0xF1 <= st_min_val <= 0xF9:
                                st_min_ms = 1  # 转换
                            fc_received = True
                            break
                if fc_received:
                    break
            time.sleep(0.001)

        if not fc_received:
            write_log("Timeout waiting for Flow Control Frame (CTS).")
            return False

        # 连续发送连续帧 (Consecutive Frames)
        remaining_data = payload[6:]
        sn = 1
        chunk_size = 7

        for i in range(0, len(remaining_data), chunk_size):
            chunk = remaining_data[i:i + chunk_size]
            cf_pci = 0x20 | (sn & 0x0F)
            cf_frame = [cf_pci] + chunk
            while len(cf_frame) < 8:
                cf_frame.append(0x00)

            # 根据 ACU 返回的 STmin 进行物理间隔延时
            time.sleep(st_min_ms / 1000.0)

            if not send_raw_can(can_dll, chn_handle, req_id, cf_frame):
                write_log(f"Failed to send Consecutive Frame (SN: {sn}).")
                return False
            sn = (sn + 1) % 16

        return True


def tp_receive(can_dll, chn_handle, res_id, req_id, timeout_ms=3000):
    """
    轻量化 ISO 15765-2 传输层接收器
    """
    buffer = []
    expected_len = 0
    received_len = 0
    seq = 1
    start_time = time.time()

    while (time.time() - start_time) < (timeout_ms / 1000.0):
        num = can_dll.GetReceiveNum(chn_handle, 0)
        if num > 0:
            msgs, ret_num = can_dll.Receive(chn_handle, num, 50)
            for i in range(ret_num):
                msg = msgs[i].frame
                if msg.can_id == res_id:
                    frame_data = list(msg.data[:msg.can_dlc])
                    if not frame_data:
                        continue

                    pci = frame_data[0] & 0xF0

                    if pci == 0x00:  # Single Frame
                        sf_len = frame_data[0] & 0x0F
                        return frame_data[1:1 + sf_len]

                    elif pci == 0x10:  # First Frame
                        expected_len = ((frame_data[0] & 0x0F) << 8) | frame_data[1]
                        buffer = frame_data[2:]
                        received_len = len(buffer)

                        # 回复流控制帧 (Flow Control - CTS, BS=0, STmin=10ms)
                        fc_frame = [0x30, 0x00, 0x0A, 0x00, 0x00, 0x00, 0x00, 0x00]
                        send_raw_can(can_dll, chn_handle, req_id, fc_frame)
                        seq = 1
                        start_time = time.time()

                    elif pci == 0x20:  # Consecutive Frame
                        if expected_len == 0:
                            continue

                        payload = frame_data[1:]
                        remaining = expected_len - received_len
                        if remaining < len(payload):
                            payload = payload[:remaining]

                        buffer.extend(payload)
                        received_len += len(payload)
                        seq = (seq + 1) & 0x0F
                        start_time = time.time()

                        if received_len >= expected_len:
                            return buffer
        else:
            time.sleep(0.001)

    return None


def uds_request(can_dll, chn_handle, UDSSID, data):
    """通过 CAN-TP 双向机制进行 UDS 交互"""
    payload = [UDSSID] + data
    req_id = DiagnosticAddressing["EDRReqAddressing"]
    res_id = DiagnosticAddressing["EDRResAddressing"]

    tx_log = ("%02X " % UDSSID) + " ".join('{:02X}'.format(a) for a in data)
    write_log(f"[UDS Tx] {req_id:03X}\t{tx_log}")

    if not tp_transmit(can_dll, chn_handle, req_id, res_id, payload):
        write_log("Request error! Hardware TP send failed.")
        return None

    response_payload = tp_receive(can_dll, chn_handle, res_id, req_id, timeout_ms=3000)

    if response_payload is None:
        write_log("Request error! Response timeout.\tNot OK")
        return None

    rx_log = " ".join('{:02X}'.format(a) for a in response_payload)
    if response_payload[0] == UDSSID + 0x40:
        write_log(f"[UDS Rx] {res_id:03X}\t{rx_log}\tPositive Response")
    elif response_payload[0] == 0x7f:
        write_log(f"[UDS Rx] {res_id:03X}\t{rx_log}\tNegative Response")
    else:
        write_log(f"[UDS Rx] {res_id:03X}\t{rx_log}\tOthers")

    return response_payload


# ==================== 安全解锁算法库加载与计算 ====================

current_dir = os.path.dirname(os.path.abspath(__file__))
ALGO_DLL_PATH = os.path.join(current_dir, "VF65ZLGDll.dll")
dll = None

try:
    if sys.version_info >= (3, 8):
        os.add_dll_directory(current_dir)
    dll = ctypes.CDLL(ALGO_DLL_PATH)
    write_log(f"✅ 安全算法库加载成功: {ALGO_DLL_PATH}")
except Exception as e:
    write_log(f"❌ 安全算法库加载失败: {e}")
    dll = None


def calculate_key_from_seed(seed_bytes: List[int], security_level: int = 3, variant: str = "VF6.PY") -> List[int]:
    """
    根据 16 字节的 Seed 调用动态链接库内的 ZLGKey 计算出 16 字节的 Key。
    对于 27 03 / 04, 解锁安全等级为 3，variant 默认为 "VF6.PY"。
    """
    if dll is None:
        raise RuntimeError("算法库未成功加载！")

    try:
        seed_array_c = (ctypes.c_ubyte * len(seed_bytes))(*seed_bytes)
        seed_array_size = ctypes.c_ushort(len(seed_bytes))
        security_level_c = ctypes.c_uint(security_level)
        variant_c = ctypes.c_char_p(variant.encode('utf-8'))

        key_array = (ctypes.c_ubyte * 16)()
        key_array_size = ctypes.c_ushort(16)

        result = dll.ZLGKey(
            seed_array_c, seed_array_size, security_level_c, variant_c,
            key_array, ctypes.byref(key_array_size)
        )

        if result != 0:
            raise RuntimeError(f"ZLGKey failed with error code: {result}")

        return list(key_array[:key_array_size.value])
    except Exception as e:
        write_log(f"ZLGKey 算法计算失败: {e}")
        raise


# ==================== 自动化核心逻辑与流程拦截 ====================

def load_cycle_times(file_path="NodeTest_option.json") -> Dict[str, int]:
    """
    从 NodeTest_option.json 的 MessagesList 中预加载所有报文的 CycleTime 定义
    返回字典结构，key为规范化的16进制字符串（不带0x的前缀且大写，如 "D9"、"20D"）
    """
    cycle_mapping = {}
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for msg_list in data.get("MessagesList", []):
                    for msg in msg_list:
                        msg_id_str = msg.get("MessageID", "").strip().upper()
                        if msg_id_str.startswith("0X"):
                            msg_id_str = msg_id_str[2:]
                        cycle_time = msg.get("CycleTime")
                        if cycle_time is not None:
                            cycle_mapping[msg_id_str] = int(cycle_time)
        except Exception as e:
            write_log(f"加载 CycleTime 配置文件失败: {e}")
    return cycle_mapping


def load_mature_times(file_path="NodeTest_option.json") -> Dict[str, int]:
    """
    从 NodeTest_option.json 的 MessagesList 中预加载所有报文的 MatureTime 定义
    返回字典结构，key为规范化的16进制字符串（不带0x的前缀且大写）
    """
    mature_mapping = {}
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for msg_list in data.get("MessagesList", []):
                    for msg in msg_list:
                        msg_id_str = msg.get("MessageID", "").strip().upper()
                        if msg_id_str.startswith("0X"):
                            msg_id_str = msg_id_str[2:]
                        mature_time = msg.get("MatureTime")
                        if mature_time is not None:
                            mature_mapping[msg_id_str] = int(mature_time)
        except Exception as e:
            write_log(f"加载 MatureTime 配置文件失败: {e}")
    return mature_mapping


def count_config_nodes(file_path="NodeTest_option.json") -> int:
    """
    从配置文件的 MessagesList 字段中统计测试节点的总数量。
    """
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                count = 0
                for msg_list in data.get("MessagesList", []):
                    count += len(msg_list)
                return count
        except Exception:
            pass
    return 0


def init_periodic_messages(dtc_file_path="NodeTest_option.json"):
    """
    初始化周期报文。
    优先加载 NodeTest_option.json 的 MessagesList 字段作为默认的仿真节点，
    同时保持与原 dtc_file_path 文件中 DTCLists 结构的后向兼容。
    """
    global periodic_messages
    temp_msgs = {}

    # 加载底层基础硬前置激活报文
    for msg in acu_preconditions:
        temp_msgs[msg["id"]] = {
            "data": msg["data"],
            "ms": msg["ms"],
            "next_send": time.time()
        }

    if os.path.exists(dtc_file_path):
        try:
            with open(dtc_file_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)

                # 兼容模式 1：读取 MessagesList （NodeTest_option.json 标准结构）
                if "MessagesList" in config_data:
                    for msg_list in config_data.get("MessagesList", []):
                        for msg in msg_list:
                            msg_id_str = msg.get("MessageID")
                            if msg_id_str:
                                try:
                                    can_id = int(msg_id_str, 16)
                                    raw_data_str = msg.get("data", "")
                                    data_list = [int(x.strip(), 16) for x in raw_data_str.split(",") if x.strip()]
                                    cycle_time = int(msg.get("CycleTime", 100))

                                    temp_msgs[can_id] = {
                                        "data": data_list,
                                        "ms": cycle_time,
                                        "next_send": time.time()
                                    }
                                except Exception as e:
                                    write_log(f"解析仿真报文 0x{msg_id_str} 失败: {e}")

                # 兼容模式 2：读取原先代码的 DTCLists 选项
                if "DTCLists" in config_data:
                    for dtc_list in config_data.get("DTCLists", []):
                        for dtc in dtc_list:
                            msg_id_str = dtc.get("RelatedMessageID")
                            if msg_id_str:
                                try:
                                    msg_id = int(msg_id_str, 16)
                                    if msg_id not in temp_msgs:
                                        temp_msgs[msg_id] = {
                                            "data": [0x00] * 8,
                                            "ms": 100,
                                            "next_send": time.time()
                                        }
                                except ValueError:
                                    pass
            write_log(f"成功从 {dtc_file_path} 加载并初始化了仿真节点，共 {len(temp_msgs)} 个。")
        except Exception as e:
            write_log(f"读取 {dtc_file_path} 时出错: {e}")
    else:
        write_log(f"警告: 未找到 {dtc_file_path}，将只发送基础 ACU 激活报文。")

    with periodic_lock:
        periodic_messages = temp_msgs


def verify_periodic_messages_status(thread_obj) -> bool:
    """
    检查周期报文后台发送状态
    """
    global stop_periodic
    if stop_periodic:
        write_log("[-] 【状态异常】周期发送控制标志 stop_periodic 处于 True (停发状态)！")
        return False
    if thread_obj is None or not thread_obj.is_alive():
        write_log("[-] 【状态异常】周期发送后台线程未运行或已被销毁！")
        return False
    with periodic_lock:
        if not periodic_messages:
            write_log("[-] 【状态异常】周期发送消息列表为空，无任何报文在队列中！")
            return False
    return True


def apply_node_overrides(overrides: Dict[str, str]):
    with periodic_lock:
        for hex_id, hex_data in overrides.items():
            try:
                can_id = int(hex_id, 16)
                data_list = [int(x, 16) for x in hex_data.strip().split()]
                if can_id in periodic_messages:
                    periodic_messages[can_id]["data"] = data_list
                else:
                    periodic_messages[can_id] = {
                        "data": data_list,
                        "ms": 100,
                        "next_send": time.time()
                    }
                write_log(f"[用例覆盖] 节点 0x{can_id:X} 数据更新为: {hex_data}")
            except Exception as e:
                write_log(f"更新节点 0x{hex_id} 数据失败: {e}")


def execute_clear_sequence(can_dll, chn_handle, clear_file_path="ClearSequence.json") -> bool:
    """
    根据 ClearSequence.json 执行诊断清除流程。
    中间步骤输出被隐藏，并在 log 中统一使用 "环境准备中..." 进行标示。
    """
    global _mute_diag_logs
    write_log("====== 开始执行 ACU 诊断清除及重置流程 ======", force=True)
    write_log("环境准备中...", force=True)

    if not os.path.exists(clear_file_path):
        write_log(f"错误: 找不到诊断清除序列配置文件 {clear_file_path}！", force=True)
        return False

    try:
        with open(clear_file_path, 'r', encoding='utf-8') as f:
            steps = json.load(f)
    except Exception as e:
        write_log(f"解析清除配置文件错误: {e}", force=True)
        return False

    max_clear_runs = 5
    run_success = False

    # 临时开启静默机制：拦截屏蔽诊断清除细节日志
    _mute_diag_logs = True
    try:
        for run in range(1, max_clear_runs + 1):
            run_success = True

            for idx, step in enumerate(steps, 1):
                name = step.get("name", "未命名步骤")
                if "delay" in step:
                    delay_ms = step["delay"]
                    time.sleep(delay_ms / 1000.0)
                    continue

                if "request" in step:
                    req_str = step["request"]
                    req_bytes = [int(x, 16) for x in req_str.split()]
                    if not req_bytes:
                        continue
                    sid = req_bytes[0]
                    params = req_bytes[1:]

                    # ------------------ 安全解锁拦截与 DLL 调用握手 ------------------
                    if sid == 0x27 and params == [0x03]:
                        # 1. 物理请求 Seed (27 03)
                        res = uds_request(can_dll, chn_handle, 0x27, [0x03])
                        if res is None or len(res) < 3 or res[0] != 0x67 or res[1] != 0x03:
                            run_success = False
                            break

                        seed = res[2:]
                        try:
                            key = calculate_key_from_seed(seed, security_level=3, variant="VF6.PY")
                        except Exception:
                            run_success = False
                            break

                        # 3. 组装多帧并发送 Key (27 04)
                        key_res = uds_request(can_dll, chn_handle, 0x27, [0x04] + key)
                        if key_res is None or len(key_res) < 2 or key_res[0] != 0x67 or key_res[1] != 0x04:
                            run_success = False
                            break

                        time.sleep(0.1)
                        continue

                    # ------------------ 普通 UDS 指令执行 ------------------
                    res = uds_request(can_dll, chn_handle, sid, params)

                    # 1. 响应未收到 (Timeout)
                    if res is None:
                        run_success = False
                        break

                    # 2. 拦截否定响应 (NRC 以 7F 开头)
                    if res[0] == 0x7F:
                        run_success = False
                        break

                    # 3. 匹配检验前缀
                    expected_res_prefix = step.get("response", "")
                    if expected_res_prefix:
                        expected_bytes = [int(x, 16) for x in expected_res_prefix.split()]
                        match = True
                        for i, b in enumerate(expected_bytes):
                            if i < len(res) and res[i] != b:
                                match = False
                                break
                        if not match:
                            run_success = False
                            break
                time.sleep(0.1)

            if run_success:
                break
            else:
                time.sleep(2.0)
    finally:
        # 解除静默机制
        _mute_diag_logs = False

    if run_success:
        write_log("====== 诊断清除及重置流程执行完毕（全部步骤校验通过） ======", force=True)
        return True
    else:
        write_log("[-] 严重错误: 达到最大重试上限 5 次，ACU 清除校验流程依然无法完全成功。", force=True)
        return False


# ==================== 0x85 点火监测模块 ====================

def get_latest_0x85_byte2(can_dll, chn_handle, duration_sec=1.5) -> int:
    """在指定时间内持续监听，并返回最新收到的 0x85 报文的第 3 字节（Byte 2，0-based）"""
    can_dll.ClearBuffer(chn_handle)
    start_time = time.time()
    latest_byte2 = -1

    while (time.time() - start_time) < duration_sec:
        num = can_dll.GetReceiveNum(chn_handle, 0)
        if num > 0:
            msgs, ret_num = can_dll.Receive(chn_handle, num, 50)
            for i in range(ret_num):
                msg = msgs[i].frame
                if msg.can_id == 0x85:
                    frame_data = list(msg.data[:msg.can_dlc])
                    if len(frame_data) >= 3:
                        latest_byte2 = frame_data[2]  # 获取第 3 个字节 (0-based 索引为 2)
        time.sleep(0.01)
    return latest_byte2


def check_is_ignited(can_dll, chn_handle) -> bool:
    """快速检查 ACU 是否已经是点火锁定状态"""
    byte2 = get_latest_0x85_byte2(can_dll, chn_handle, duration_sec=1.5)
    if byte2 == -1:
        # 监听到 0x85 超时，默认视为未点火
        return False
    # 0x01 表示未点火，其余数值均表示已点火
    return byte2 != 0x01


def wait_for_ignition_trigger(can_dll, chn_handle, timeout_sec=60.0, wait_delay_ms=5000) -> bool:
    """
    监听 ACU 是否触发点火。
    在收到碰撞敲击指令前，先主动等待指定的时间 (MatureTime * 10)，清空缓冲区，最后输出敲击提示并进入实时点火信号侦测。
    """
    # 1. 先进行主动延时等待 (MatureTime * 10)
    write_log(
        f"【等待】主动延时等待所测节点 MatureTime 十倍的时间: {wait_delay_ms} ms...")
    time.sleep(wait_delay_ms / 1000.0)

    # 2. 延时等待结束后，清空缓冲区（丢弃延时期间产生的旧报文）
    can_dll.ClearBuffer(chn_handle)

    # 3. 延时结束后输出提示信息
    write_log("【提示】当前已被确认为未点火状态，请开始模拟碰撞敲击 ACU！等待点火信号中...")
    start_time = time.time()

    while True:
        # 检查是否等待超时
        if (time.time() - start_time) >= timeout_sec:
            write_log(f"【超时】已达到等待上限时间（{timeout_sec}秒），未收到点火信号。")
            return False

        byte2 = get_latest_0x85_byte2(can_dll, chn_handle, duration_sec=1.0)
        if byte2 != -1 and byte2 != 0x01:
            return True

        time.sleep(0.1)


# ==================== 数据解析及文件保存逻辑 ====================

def convert_to_filtered_ascii(signal_values):
    result = []
    for v in signal_values:
        try:
            if 0x20 <= v <= 0x7E:
                result.append(chr(v))
            else:
                result.append(f'<0x{v:02X}>')
        except (ValueError, TypeError):
            result.append('<Invalid>')
    return ''.join(result)


def process_array_data(raw_array: List[int], config: Dict[str, Any]) -> Dict[str, Any]:
    if len(raw_array) != paramDataLength:
        write_log(f"Error: raw_array length is {len(raw_array)}, expected {paramDataLength}.")
        return {}
    processed_data = {}
    start = 0
    for signal_name, signal_config in config.items():
        end = start + signal_config.get("Length", 0)
        length = signal_config.get("SingleSignalLength", 1)
        factor = signal_config.get("Factor", 1)
        offset = signal_config.get("Offset", 0)
        data_type = signal_config.get("Type", "raw")

        if end > len(raw_array):
            write_log(f"Error: {signal_name} end index {end} exceeds raw_array length {len(raw_array)}.")
            return {}

        signal_values = raw_array[start:end]

        if data_type.lower() == "ascii":
            try:
                value = convert_to_filtered_ascii(signal_values)
            except (ValueError, TypeError):
                value = "Invalid ASCII"
        else:
            values = []
            for i in range(0, len(signal_values), length):
                chunk = signal_values[i:i + length]
                if not chunk:
                    continue
                try:
                    if length == 1:
                        if chunk[0] in (0xFE, 0xFF):
                            if all(v in (0xFE, 0xFF) for v in signal_values[i:]):
                                values.extend(signal_values[i:])
                                break
                            else:
                                processed_num = round(chunk[0] * factor + offset, 3)
                                values.append(processed_num)
                        else:
                            processed_num = round(chunk[0] * factor + offset, 3)
                            values.append(processed_num)

                    elif length == 2:
                        num = (chunk[0] << 8) | chunk[1]
                        if num in (0xFFFF, 0xFFFE):
                            all_special = True
                            for j in range(i, len(signal_values), 2):
                                next_chunk = signal_values[j:j + 2]
                                if len(next_chunk) < 2:
                                    continue
                                next_num = (next_chunk[0] << 8) | next_chunk[1]
                                if next_num not in (0xFFFF, 0xFFFE):
                                    all_special = False
                                    break
                            if all_special:
                                values.extend(signal_values[i:])
                                break
                            else:
                                processed_num = round(num * factor + offset, 3)
                                values.append(processed_num)
                        else:
                            processed_num = round(num * factor + offset, 3)
                            values.append(processed_num)

                    elif length == 3:
                        num = (chunk[0] << 16) | (chunk[1] << 8) | chunk[2]
                        if num in (0xFFFFFF, 0xFFFFFE):
                            all_special = True
                            for j in range(i, len(signal_values), 3):
                                next_chunk = signal_values[j:j + 3]
                                if len(next_chunk) < 3:
                                    continue
                                next_num = (next_chunk[0] << 16) | (next_chunk[1] << 8) | next_chunk[2]
                                if next_num not in (0xFFFFFF, 0xFFFFFE):
                                    all_special = False
                                    break
                            if all_special:
                                values.extend(signal_values[i:])
                                break
                            else:
                                processed_num = round(num * factor + offset, 3)
                                values.append(processed_num)
                        else:
                            processed_num = round(num * factor + offset, 3)
                            values.append(processed_num)

                    elif length == 4:
                        num = (chunk[0] << 24) | (chunk[1] << 16) | (chunk[2] << 8) | chunk[3]
                        if num in (0xFFFFFFFF, 0xFFFFFFFE):
                            all_special = True
                            for j in range(i, len(signal_values), 4):
                                next_chunk = signal_values[j:j + 4]
                                if len(next_chunk) < 4:
                                    continue
                                next_num = (next_chunk[0] << 24) | (next_chunk[1] << 16) | (next_chunk[2] << 8) | \
                                           next_chunk[3]
                                if next_num not in (0xFFFFFFFF, 0xFFFFFFFE):
                                    all_special = False
                                    break
                            if all_special:
                                values.extend(signal_values[i:])
                                break
                            else:
                                processed_num = num * factor + offset
                                values.append(processed_num)
                        else:
                            processed_num = num * factor + offset
                            values.append(processed_num)

                    else:
                        num = sum(v << (8 * (len(chunk) - 1 - i)) for i, v in enumerate(chunk))
                        processed_num = num * factor + offset
                        values.append(processed_num)

                except Exception as e:
                    write_log(f"Error processing numerical data for {signal_name}: {e}")
                    values.append(None)

            value = values if len(values) > 1 else values[0] if values else None

        processed_data[signal_name] = {
            "Description": signal_config.get("Description", ""),
            "Value": value,
            "RawData": signal_values
        }
        start = end
    return processed_data


# ==================== 测试汇总报告格式化输出 ====================

def print_final_summary(pass_cnt, fail_cnt, skip_cnt):
    """
    根据用户给定的汇总格式规范，格式化并保存/打印 VF6N_EDR 测试的所有统计细节。
    """
    total_executed = pass_cnt + fail_cnt + skip_cnt
    pass_rate = 0
    if total_executed > 0:
        pass_rate = int(round((pass_cnt / total_executed) * 100))

    node_count = count_config_nodes("NodeTest_option.json")
    abs_log_path = os.path.abspath(log_file_path) if log_file_path else "Unknown"
    now_dt_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def write_summary_line(msg):
        # 强制格式：[YYYY/MM/DD-HH:MM:SS.ffffff] INFO:{msg}
        ts = datetime.datetime.now().strftime("%Y/%m/%d-%H:%M:%S.%f")
        line = f"{ts} INFO:{msg}"
        print(line)
        if log_file_path:
            try:
                with open(log_file_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    # 开头的分隔线无 timestamp
    divider = "=================================================="
    print(divider)
    if log_file_path:
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(divider + "\n")
        except Exception:
            pass

    write_summary_line("📊 所有节点测试完成 - 整体统计汇总")
    write_summary_line("==================================================")
    write_summary_line(f"  完成时间:      {now_dt_str}")
    write_summary_line(f"  测试节点总数:  {node_count} 个")
    write_summary_line("--------------------------------------------------")
    write_summary_line(f"  有效执行测试项: {total_executed}")
    write_summary_line(f"  成功 (PASS):    {pass_cnt}")
    write_summary_line(f"  失败 (FAIL):    {fail_cnt}")
    write_summary_line(f"  跳过 (SKIP):    {skip_cnt}")
    write_summary_line("--------------------------------------------------")
    write_summary_line(f"  整体通过率:     {pass_rate}%")
    write_summary_line("==================================================")
    write_summary_line(f"详细记录已保存至: {abs_log_path}")

    # 测试项运行数输出，无 timestamp
    test_run_line = f"\n[==========] {total_executed} test(s) run."
    print(test_run_line)
    if log_file_path:
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(test_run_line + "\n")
        except Exception:
            pass

    write_summary_line("CloseDevice success.")


# ==================== 主控自动化测试流程入口 ====================

thread_flag = True


def run_auto_test():
    global stop_periodic, log_file_path, thread_flag

    # 建立 Logs 文件夹，并生成用于 outputReport 解析的物理日志文件
    os.makedirs("Logs", exist_ok=True)
    now_time_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = f"Logs/EDR_TestRun_{now_time_str}.log"

    write_log("====== 启动 VF6N_EDR 自动化验证控制脚本 ======")
    write_log(f"测试日志正在记录到: {log_file_path}")

    # 预加载节点 MatureTime 延迟列表
    node_mature_times = load_mature_times("NodeTest_option.json")

    # 统计数据初识化
    pass_count = 0
    fail_count = 0
    skip_count = 0

    can_dll = ZCAN()
    dev_handle = can_dll.OpenDevice(DEVICE_TYPE, DEVICE_INDEX, 0)
    if dev_handle == 0:
        write_log("Error: Open Device Failed!")
        return

    # 💡【重要改进：Monkey Patch 拦截并包装原驱动的 Receive 接收功能】
    # 这样无论是诊断接收（tp_receive）还是信号监听（get_latest_0x85_byte2）
    # 只要驱动底层拿到了 Rx 报文，都能在这里被统一异步记录到 Bus_Traffic 日志中。
    original_receive = can_dll.Receive

    def wrapped_receive(chn_handle, num, timeout=50):
        msgs, ret_num = original_receive(chn_handle, num, timeout)
        for i in range(ret_num):
            msg = msgs[i].frame
            raw_data = list(msg.data[:msg.can_dlc])
            log_bus_traffic("Rx", msg.can_id, msg.can_dlc, raw_data, can_type="CAN", channel=CHANNEL_INDEX)
        return msgs, ret_num

    can_dll.Receive = wrapped_receive

    iproperty = can_dll.GetIProperty(dev_handle)
    if iproperty:
        baud_path = f"info/channel/chn{CHANNEL_INDEX}/baudrate"
        can_dll.SetValue(iproperty, baud_path, BAUD_RATE)
        can_dll.ReleaseIProperty(iproperty)

    init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    init_cfg.can_type = 0  # 标准 CAN
    init_cfg.config.can.acc_code = 0x00000000
    init_cfg.config.can.acc_mask = 0xFFFFFFFF
    init_cfg.config.can.filter = 0
    init_cfg.config.can.mode = 0

    chn_handle = can_dll.InitCAN(dev_handle, CHANNEL_INDEX, init_cfg)
    if chn_handle == 0:
        write_log("Error: Init CAN Channel Failed!")
        can_dll.CloseDevice(dev_handle)
        return

    if can_dll.StartCAN(chn_handle) != ZCAN_STATUS_OK:
        write_log("Error: Start CAN Failed!")
        can_dll.CloseDevice(dev_handle)
        return

    # 初始化并启动总线日志缓冲线程与周期发送仿真线程
    init_traffic_log()

    thread_flag = True
    writer_thread = threading.Thread(target=traffic_writer_thread_func)
    writer_thread.daemon = True
    writer_thread.start()

    init_periodic_messages("NodeTest_option.json")

    stop_periodic = False
    sender_thread = threading.Thread(target=periodic_sender_loop, args=(can_dll, chn_handle))
    sender_thread.daemon = True
    sender_thread.start()
    write_log("周期报文后台仿真线程已启动并开始发送工作。")

    # 加载用例 YAML 文件
    yaml_path = "VF6N_EDR_Testcases.yaml"
    if not os.path.exists(yaml_path):
        write_log(f"错误: 找不到测试用例配置文件 {yaml_path}")
        stop_periodic = True
        thread_flag = False
        can_dll.CloseDevice(dev_handle)
        return

    with open(yaml_path, 'r', encoding='utf-8') as f:
        suite = yaml.safe_load(f)

    test_cases = suite.get("test_cases", [])
    if not test_cases:
        write_log("未在 YAML 中检测到任何测试用例。")
        stop_periodic = True
        thread_flag = False
        can_dll.CloseDevice(dev_handle)
        return

    config = json.loads(jsonData)
    total_cases = len(test_cases)

    for case_idx, case in enumerate(test_cases, 1):
        case_name = case.get('name', f'TestCase_{case_idx}')
        write_log(
            f"\n\n==================== [开始执行用例 ({case_idx}/{total_cases}): {case_name}] ====================")
        write_log(f"用例描述: {case.get('description')}")

        # 满足 outputReport 要求的启动标志 (重要：这一行让 outputReport 捕获用例开始)
        write_log(f"[ RUN      ] {case_name}")

        # 检验周期后台仿真发送状态
        write_log("【状态检查】正在检验周期后台仿真发送状态...")
        init_periodic_messages("NodeTest_option.json")  # 重置并拉起默认配置数据
        if not verify_periodic_messages_status(sender_thread):
            write_log("[-] 周期发送状态异常，正在尝试重新开启后台发送...")
            stop_periodic = False
            if sender_thread is None or not sender_thread.is_alive():
                sender_thread = threading.Thread(target=periodic_sender_loop, args=(can_dll, chn_handle))
                sender_thread.daemon = True
                sender_thread.start()
                time.sleep(0.5)

            if not verify_periodic_messages_status(sender_thread):
                write_log("[-] [ ERROR ] 严重错误: 周期发送无法恢复，用例终止，安全关闭通道！")
                write_log(f"[   FAILED ] {case_name}")
                fail_count += 1
                stop_periodic = True
                thread_flag = False
                can_dll.CloseDevice(dev_handle)
                print_final_summary(pass_count, fail_count, skip_count)
                return
        else:
            write_log("[+] 周期仿真报文已全部确认处于持续发送中状态。")

        # 执行清除序列与复位状态校验
        clear_attempts = 0
        while True:
            clear_attempts += 1
            write_log(f"正在进行第 {clear_attempts} 次整套诊断清除、解锁、重置流程...")

            # 执行清除流程（内部细节已被全部隐藏）
            clear_success = execute_clear_sequence(can_dll, chn_handle, "ClearSequence.json")
            if not clear_success:
                write_log("[-] 【关键校验失败】诊断清除序列中存在校验不匹配步骤，正在触发重新清除重置...")
                if clear_attempts >= 3:
                    write_log(
                        "【警告】连续 3 次诊断清除完全失败！请检查物理线束、解锁 DLL、ACU 配置字，按回车尝试重新运行清除流程。")
                    input("解决连接或算法问题后，请按 [回车键] 再次开始清除流程...")
                    clear_attempts = 0
                time.sleep(1.0)
                continue

            # 延时 1s 让复位后的状态稳定
            time.sleep(1.0)

            # 检查清除后是否回到未点火状态
            write_log("正在检查清除复位后的 ACU 锁定状态...")
            is_ignited = check_is_ignited(can_dll, chn_handle)

            if not is_ignited:
                write_log("【状态确认】清除完全通过！当前 ACU 确认为未点火锁定状态（安全），允许执行后续用例和物理敲击。")
                break
            else:
                write_log("【警告】诊断清除校验全部通过，但 0x85 报文依然显示为点火锁定状态。重新执行清除流程...")
                if clear_attempts >= 5:
                    write_log("【严重错误】连续 5 次执行清除成功，但 ACU 硬件仍无法解除锁定，请确认算法逻辑！")
                    input("检查解密算法及设备连接后，按 [回车键] 重新尝试当前清除流程...")
                    clear_attempts = 0

        # 修改覆盖仿真报文
        overrides = case.get("node_overrides", {})
        if overrides:
            apply_node_overrides(overrides)

        # 满足新逻辑：计算当前覆盖节点的 MatureTime * 10 对应的主动等待延时
        wait_delay_ms = 5000  # fallback 500ms * 10 = 5000ms
        if overrides:
            for hex_id in overrides.keys():
                norm_id = hex_id.strip().upper()
                if norm_id.startswith("0X"):
                    norm_id = norm_id[2:]
                if norm_id in node_mature_times:
                    wait_delay_ms = node_mature_times[norm_id] * 10
                    break

        # 点火触发判定，传参使用新的 wait_delay_ms
        if not wait_for_ignition_trigger(can_dll, chn_handle, timeout_sec=60.0, wait_delay_ms=wait_delay_ms):
            write_log("[-] [ ERROR ] 检测超时，测试被迫中止。")
            write_log(f"[   FAILED ] {case_name}")
            fail_count += 1
            break

        # 成功触发点火后立刻还原周期节点
        write_log("【成功点火】检测到成功点火触发，开始还原周期发送节点的报文为默认状态...")
        init_periodic_messages("NodeTest_option.json")

        write_log("正在延时等待诊断响应（给 ACU VF6N_EDR 数据写入缓冲时间）...")
        time.sleep(1.0)

        # 通过 UDS 诊断读取锁定数据并记录 (依次读取 FA 03, FA 04, FA 05)
        dids_to_read = [
            {"name": "FA03", "sub_fn": [0xFA, 0x03]},
            {"name": "FA04", "sub_fn": [0xFA, 0x04]},
            {"name": "FA05", "sub_fn": [0xFA, 0x05]}
        ]

        all_processed_data = {}
        read_success = True

        for did_info in dids_to_read:
            did_name = did_info["name"]
            sub_fn = did_info["sub_fn"]

            write_log(f"开始通过诊断读取并解析 VF6N_EDR 锁定数据（22 {' '.join(f'{b:02X}' for b in sub_fn)}）...")
            raw_array = uds_request(can_dll, chn_handle, 0x22, sub_fn)

            if raw_array and len(raw_array) > 3:
                processed_data = process_array_data(raw_array[3:], config)
                all_processed_data[did_name] = processed_data

                # 过滤文件名中的非法字符
                safe_case_name = "".join(c for c in case_name if c not in r'\\/:*?"<>|').strip().replace(" ", "_")

                # 保存本地 JSON 数据
                now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                save_path = f"Data/{safe_case_name}_{did_name}_{now_str}.json"

                os.makedirs("Data", exist_ok=True)
                with open(save_path, 'w', encoding='utf-8') as sf:
                    json.dump(processed_data, sf, indent=4)
                write_log(f"已将读取到的 VF6N_EDR 数据转存至: {save_path}")
            else:
                write_log(f"[-] [ FAILED ] 无法获取 UDS {did_name} 的有效回复！")
                read_success = False

        if read_success:
            # 数据强验证 (自动在 FA 03, FA 04, FA 05 的解析结果中检索并校验目标信号)
            rules = case.get("expected_edr", {})
            target_sig = rules.get("signal_name")
            target_val = rules.get("target_value")
            tolerance = rules.get("tolerance", 0.1)

            found_signal = False
            for did_name, processed_data in all_processed_data.items():
                if target_sig in processed_data:
                    found_signal = True
                    actual_val_field = processed_data[target_sig].get("Value")
                    actual_val = actual_val_field[0] if isinstance(actual_val_field, list) else actual_val_field

                    write_log(f"【数据校验】 目标信号: '{target_sig}' (源自 {did_name})")
                    write_log(f"【数据校验】 预期值: {target_val}，实际读取值: {actual_val}")

                    if actual_val is not None and abs(actual_val - target_val) <= tolerance:
                        write_log("[ OK ] 数据校验一致，测试用例通过！")
                        write_log(f"[       OK ] {case_name}")
                        pass_count += 1
                    else:
                        write_log("[-] [ FAILED ] 数据校验异常，目标信号值不符！")
                        write_log(f"[   FAILED ] {case_name}")
                        fail_count += 1
                    break

            if not found_signal:
                write_log(f"[-] [ FAILED ] 未在解析的 VF6N_EDR 字段 (FA03/FA04/FA05) 中找到信号: {target_sig}")
                write_log(f"[   FAILED ] {case_name}")
                fail_count += 1
        else:
            write_log(f"[   FAILED ] {case_name}")
            fail_count += 1

        write_log(f"==================== [用例 {case_name} 执行完毕] ====================\n")

        # 测试间隔时间
        if case_idx < total_cases:
            write_log("正在等待 5.0 秒，即将开始执行下一个测试用例...")
            time.sleep(5.0)

    # 释放硬件资源
    stop_periodic = True
    thread_flag = False
    sender_thread.join(timeout=1.0)
    writer_thread.join(timeout=1.0)
    can_dll.ResetCAN(chn_handle)
    can_dll.CloseDevice(dev_handle)

    # 输出所有测试汇总记录
    print_final_summary(pass_count, fail_count, skip_count)


if __name__ == "__main__":
    run_auto_test()