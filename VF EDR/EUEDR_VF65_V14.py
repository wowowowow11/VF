"""
File Overview:

## File Basic Information
- **File Name**: EUEDR_VF65_V13.py
- **Version Information**: V1.3
- **Author**: DuanZhaobing
- **Date**: 2025.8.14

## File Functionality
    This Python script is primarily used for communication with ACU,
    sending requests and processing response data via the Unified Diagnostic Services (UDS) protocol.
    The script also includes data parsing and saving functionality, storing the processed EDR data as JSON format files.

## Main Functional Modules
1. **Imported Modules**:
    - `json`: For handling JSON data.
    - `random`: For generating random numbers.
    - `datetime`: For obtaining the current system time.
    - `zcanpro`: For automotive diagnostic communication.
    - `time`, `threading`, `os`: For time control, multithreading, and file operations.

2. **Global Variables**:
    - `stopTask`: Controls whether the task should stop.
    - `timeDelaySec`: Time delay setting.
    - `DiagnosticAddressing`: Diagnostic address configuration.
    - `NRCDefinition`: Negative response code definitions.
    - `UDSConfiguration`: UDS diagnostic information configuration.

3. **Functions**:
    - `uds_request`: Sends UDS requests and processes responses.
    - `z_notify`: Notification function for handling stop task notifications.
    - `process_array_data`: Processes array data based on configuration.
    - `save_to_json`: Saves processed data to a JSON file.
    - `generate_random_array`: Generates random arrays.
    - `z_main`: Main function that executes the main logic.

## Usage
1. Ensure that the `zcanpro` library is installed for automotive diagnostic communication.
2. Run the script, which will automatically perform the following operations:
    - Initialize UDS configuration.
    - Send UDS requests and receive responses.
    - Parse response data.
    - Save parsed data as JSON files.
"""

import json
import random
import datetime
import zcanpro
import time, threading
import time
import os

from typing import Dict, List, Any

stopTask = False

timeDelaySec = 0.05
stopTask = False
paramDataLength = 898  # 参数数据长度
proName = "VF65"

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
        "Factor": 1,
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

# 定义寻址
DiagnosticAddressing = {
    "EDRReqAddressing": 0x688,
    "EDRResAddressing": 0x608
}

#########################################################
# NRC
NRCDefinition = {
    "serviceNotSupported": 0x11,  # Mnemonic SNS
    "subfunctionNotSupported": 0x12,  # Mnemonic SFNS
    "incorrectMessageLengthOrInvalidFormat": 0x13,  # Mnemonic IMLOIF
    "conditionsNotCorrect": 0x22,  # Mnemonic CNC
    "requestOutOfRange": 0x31,  # Mnemonic ROOR
    "securityAccessDenied": 0x33,  # Mnemonic SAD
    "invaledKey": 0x35,  # Mnemonic IK
    "exceedNumberOfAttempts": 0x36,  # Mnemonic ENOA
    "requiredTimeDelayNotExpired": 0x37,  # Mnemonic RTDNE
    "requestCorrectlyReceivedResponsePending": 0x78,  # Mnemonic RCRRP
    "serviceNotSupportedInActiveSession": 0x7f  # Mnemonic SFNSIAS
}

#########################################################
# UDS诊断信息配置
UDSConfiguration = {
    "response_timeout_ms": 3000,  # 响应超时时间(ms)
    "use_canfd": 0,  # 是否使用CANFD, 0-CAN, 1-CANFD
    "canfd_brs": 0,  # CANFD加速(使用CANFD时有效), 0-不加速, 1-加速
    "trans_ver": 0,  # 传输协议版本, 0-ISO15765_2 2004格式, 1-ISO15765_2 2016新增格式
    "fill_byte": 0x00,  # 填充字节,如使用CAN发送时,数据不足8字节,将填充至8字节进行发送
    "frame_type": 0,  # 帧类型,0-标准帧,1-扩展帧
    "trans_stmin_valid": 0,  # 是否设置多帧发送的STmin,替代ECU流控返回的STmin
    "trans_stmin": 0,  # 多帧发送的STmin最小帧间隔时间(ms),范围 [0, 127]
    "enhanced_timeout_ms": 5000,  # 当消极响应值为0x78时延长的超时时间(ms)
    "fc_timeout_ms": 1000,  # 接收流控超时时间(ms), 如发送首帧后需要等待回应流控帧, 范围 [20, 10000]
    "fill_mode": 1,  # 数据长度填充模式, 0-不填充；1-小于8字节填充至8字节,大于8字节时按DLC就近填充；2-填充至最大数据长度 (不建议)
}


# UDSRequest For Positive Response In Physical Addressing
def uds_request(busID, UDSSID, data):
    zcanpro.uds_init(UDSConfiguration)
    req = {
        "src_addr": DiagnosticAddressing["EDRReqAddressing"],
        "dst_addr": DiagnosticAddressing["EDRResAddressing"],
        "suppress_response": 0,
        "sid": UDSSID,
        "data": data
    }

    zcanpro.write_log(
        "[UDS Tx] " + str('{:03X}'.format(req["src_addr"])) + '\t' + ("%02X " % req["sid"]) + " ".join(
            '{:02X}'.format(a) for a in req["data"]))
    response = zcanpro.uds_request(busID, req)
    if not response["result"]:
        zcanpro.write_log("Request error! " + response["result_msg"] + '\t' + "Not OK")
    elif response["data"][0] == req["sid"] + 0x40:
        zcanpro.write_log("[UDS Rx] " + str('{:03X}'.format(req["dst_addr"])) + '\t' + " ".join(
            '{:02X}'.format(a) for a in response["data"]) + '\t' + "Positive Response")
    elif response["data"][0] == 0x7f:
        zcanpro.write_log("[UDS Rx] " + str('{:03X}'.format(req["dst_addr"])) + '\t' + " ".join(
            '{:02X}'.format(a) for a in response["data"]) + '\t' + "Negative Response")
    else:
        zcanpro.write_log("[UDS Rx] " + str('{:03X}'.format(req["dst_addr"])) + '\t' + " ".join(
            '{:02X}'.format(a) for a in response["data"]) + '\t' + "Others")

    zcanpro.uds_deinit()
    return response["data"]


def z_notify(type, obj):
    zcanpro.write_log("Notify " + str(type) + " " + str(obj))
    if type == "stop":
        zcanpro.write_log("Stop...")
        global stopTask
        stopTask = True


def convert_to_filtered_ascii(signal_values):
    result = []
    for v in signal_values:
        try:
            # 检查是否在可打印ASCII范围内
            if 0x20 <= v <= 0x7E:
                result.append(chr(v))
            else:
                # 不在可打印范围内，显示原始值（可选择十六进制或十进制）
                result.append(f'<0x{v:02X}>')  # 十六进制显示
                # 或者 result.append(f'<{v}>')  # 十进制显示
        except (ValueError, TypeError):
            result.append('<Invalid>')
    return ''.join(result)


def process_array_data(raw_array: List[int], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    处理数组形式的原始数据并根据配置转换为有意义的数值

    :param raw_array: 原始数据数组（整数列表）
    :param config: 配置字典
    :return: 包含处理后的数据的字典
    """
    if raw_array.__len__() != paramDataLength:
        zcanpro.write_log(f"Error: raw_array length is {raw_array.__len__()}, expected {paramDataLength}.")
        return {}
    processed_data = {}
    start = 0
    for signal_name, signal_config in config.items():
        # 获取信号配置参数
        end = start + signal_config.get("Length", 0)
        length = signal_config.get("SingleSignalLength", 1)
        factor = signal_config.get("Factor", 1)
        offset = signal_config.get("Offset", 0)
        data_type = signal_config.get("Type", "raw")
        # zcanpro.write_log(f"Processing signal: {str(signal_name)}, Start: {str(start)}, End: {str(end - 1)}")
        if end > raw_array.__len__():
            zcanpro.write_log(f"Error: {signal_name} end index {end} exceeds raw_array length {raw_array.__len__()}.")
            return {}

        # 提取对应的数据片段, 左闭右开
        signal_values = raw_array[start:end]

        # 根据数据类型处理
        if data_type.lower() == "ascii":
            # ASCII 字符串处理 - 将数值转换为字符
            try:
                value = convert_to_filtered_ascii(signal_values)
            except (ValueError, TypeError):
                value = "Invalid ASCII"
        else:
            # 原始数据处理
            values = []
            for i in range(0, len(signal_values), length):
                chunk = signal_values[i:i + length]
                if not chunk:
                    continue
                # 处理数值数据
                try:
                    if length == 1:
                        # 特殊处理：遇到第一个为0xFE或0xFF，且后面所有数据皆为0xFE或0xFF，才append剩余原始数据，否则继续处理
                        if chunk[0] in (0xFE, 0xFF):
                            if all(v in (0xFE, 0xFF) for v in signal_values[i:]):
                                values.extend(signal_values[i:])
                                break
                            else:
                                num = chunk[0]
                                processed_num = round(num * factor + offset, 3)
                                values.append(processed_num)
                        else:
                            num = chunk[0]
                            processed_num = round(num * factor + offset, 3)
                            values.append(processed_num)

                    elif length == 2:
                        num = (chunk[0] << 8) | chunk[1]
                        # 特殊处理：遇到第一个为0xFFFF或0xFFFE，且后面所有num也全为0xFFFF或0xFFFE，才append剩余原始数据，否则继续处理
                        if num in (0xFFFF, 0xFFFE):
                            # 检查后续所有num是否都为0xFFFF或0xFFFE
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
                        # 特殊处理：遇到第一个为0xFFFFFF或0xFFFFFE，且后面所有num也全为这两种，才append剩余原始数据，否则继续处理
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
                        # 特殊处理：遇到第一个为0xFFFFFFFF或0xFFFFFFFE，且后面所有num也全为这两种，才append剩余原始数据，否则继续处理
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
                    zcanpro.write_log(f"Error processing numerical data for {signal_name}: {e}")
                    values.append(None)

            value = values if len(values) > 1 else values[0] if values else None

        # 将处理后的值保存到结果字典
        processed_data[signal_name] = {
            "Description": signal_config.get("Description", ""),
            "Value": value,
            # "Unit": signal_config.get("Unit", ""),
            "RawData": signal_values  # 保存原始数组以便调试
        }
        start = end  # 更新起始位置
    return processed_data


def save_to_json(data: Dict[str, Any], file_name: str):
    """
    将处理后的数据保存到JSON文件

    :param data: 要保存的数据
    :param file_name: 目标文件名
    """
    now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    current_dir = os.path.dirname(__file__)
    data_dir = os.path.join(current_dir, "Data")

    if not os.path.exists(data_dir):
        os.makedirs(data_dir)

    full_path = os.path.join(data_dir, f"{proName}_{file_name}_{now}.json")

    with open(full_path, 'w') as f:
        json.dump(data, f, indent=4)


def print_data(processed_data: Dict[str, Any]):
    """
    打印处理后的数据

    :param data: 要打印的数据
    """
    if processed_data:
        for signal_name, data in processed_data.items():
            zcanpro.write_log(f"{signal_name}:")
            zcanpro.write_log(f"  Processed: {data.get('Value', 'N/A')}")

        #   RawData 可能会很长，为了避免输出过多，可以只打印一部分
        #   raw_data_display = data.get('RawData', [])
        #   if len(raw_data_display) > 20: # 如果原始数据超过20个字节，只显示前10个和后10个
        #       zcanpro.write_log(f"  原始数据: {raw_data_display[:10]} ... {raw_data_display[-10:]} (总长度: {len(raw_data_display)})")
        #   else:
        #     zcanpro.write_log(f"  原始数据: {raw_data_display}")
        #   zcanpro.write_log(f"  RawData: {data.get('RawData', [])}")
        #   zcanpro.write_log("-" * 30) # 分隔线，方便阅读
    else:
        zcanpro.write_log("No data has been processed. Please check the original data or configuration file.")


# 示例使用方式
if __name__ == "__main__":
    # 1. 加载配置文件
    with open('config.json') as f:
        config = json.load(f)

    # 2. 示例原始数据数组（替换为实际数据）
    # 修改为了895的长度，避免本地调试时由于长度不匹配被 process_array_data 拦截
    raw_array = [1] * 898

    # 3. 处理数据
    processed_data = process_array_data(raw_array, config)

    # 4. 保存处理后的数据
    save_to_json(processed_data, 'processed_data.json')

    print(f"数据处理完成并已保存到 processed_data.json")
    print(f"共处理了 {len(config)} 个信号")


def generate_random_array(length):
    return [random.randint(0, 255) for _ in range(length)]


def z_main():
    buses = zcanpro.get_buses()
    zcanpro.write_log("Get buses: " + str(buses))
    zcanpro.write_log("Bues len: " + str(len(buses)))
    config = json.loads(jsonData)

    start_time = time.time()
    zcanpro.write_log('开始时间%s' % time.ctime())
    # raw_array = generate_random_array(paramDataLength + 3)
    # raw_array = [0xFE] * (paramDataLength + 3)

    raw_array1 = uds_request(buses[0]["busID"], 0x22, [0xFA, 0x03])
    processed_data = process_array_data(raw_array1[3:], config)
    save_to_json(processed_data, '22_FA_03')
    print_data(processed_data)
    time.sleep(0.5)

    raw_array2 = uds_request(buses[0]["busID"], 0x22, [0xFA, 0x04])
    processed_data = process_array_data(raw_array2[3:], config)
    save_to_json(processed_data, '22_FA_04')
    print_data(processed_data)
    time.sleep(0.5)

    raw_array3 = uds_request(buses[0]["busID"], 0x22, [0xFA, 0x05])
    processed_data = process_array_data(raw_array3[3:], config)
    save_to_json(processed_data, '22_FA_05')
    print_data(processed_data)

    json_data = {
        "data1": raw_array1,
        "data2": raw_array2,
        "data3": raw_array3
    }

    # 保存到JSON文件
    with open("output.json", 'w') as f:
        json.dump(json_data, f, indent=4)

    run_times = (time.time() - start_time)
    zcanpro.write_log('运行时间%s' % run_times)