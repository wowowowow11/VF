# -*- coding: utf-8 -*-
import os
import sys
import time
import queue
import ctypes
import logging
import threading
from zlgcan import *

logger = logging.getLogger("UDSClient")


class HexList(list):
    """
    专为车载诊断设计的 16 进制整型列表类。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.assembled_data = None  # 用于附加拼接完成的完整多帧诊断数据（不含 ISO-TP 头）

    def __repr__(self):
        return "[" + ", ".join(f"0x{x:02X}" for x in self) + "]"

    def __str__(self):
        return " ".join(f"{x:02X}" for x in self)

class ZlgUdsClient:
    def __init__(self, device_type=ZCAN_USBCANFD_200U, port=0, req_id=0x688, resp_id=0x608, func_id=0x6FF):
        self.device_type = device_type
        self.port = port
        self.req_id = req_id  # 物理发送 ID
        self.resp_id = resp_id  # 物理接收 ID
        self.func_id = func_id  # 功能广播发送 ID
        self.dll = None

        self.device_handle = INVALID_DEVICE_HANDLE
        self.chn_handle = INVALID_CHANNEL_HANDLE
        self.zcanlib = ZCAN()

        self.rx_queue = queue.Queue()
        self.rx_thread = None
        self.thread_flag = False

        # 记录当前已经在 ZLG 硬件中不间断发送的底层背景报文配置
        self.current_rcv_node_id = None

    def update_specific_node(self, target_node_id, new_data=None, new_period=None):
        """
        修改指定节点报文的数据(data)或周期(period)
        :param target_node_id: 需要修改的节点 ID，如 "0x20D" 或 0x20D
        :param new_data: 新的数据字节列表，如 ["0xFF", "0x00", ...] (可选)
        :param new_period: 新的发送周期，毫秒，如 50 (可选)
        """
        if not self.current_rcv_node_id:
            logger.warning("当前没有正在运行的背景报文。")
            return

        target_id_int = int(target_node_id, 16) if isinstance(target_node_id, str) else int(target_node_id)

        # 遍历背景报文配置，替换对应 ID 的数据或周期
        updated_node_list = []
        is_found = False

        for node in self.current_rcv_node_id:
            curr_id = int(node.get("nodeID"), 16) if isinstance(node.get("nodeID"), str) else int(node.get("nodeID"))
            if curr_id == target_id_int:
                is_found = True
                node_copy = dict(node)  # 浅拷贝当前节点配置
                if new_data is not None:
                    node_copy["data"] = new_data
                if new_period is not None:
                    node_copy["period"] = new_period
                updated_node_list.append(node_copy)
            else:
                updated_node_list.append(node)

        if not is_found:
            logger.warning(f"⚠️ 未找到需要修改的背景节点: 0x{target_id_int:03X}")
            return

        logger.info(f"✏️ [节点控制] 正在修改节点 0x{target_id_int:03X} 的配置...")

        # 重新应用更新后的配置列表到 ZLG 硬件
        self.setup_background_messages(updated_node_list)

    def stop_specific_node(self, target_node_id):
        """
        动态停发指定的某一个节点报文 (例如 "0x20D" 或 0x20D)，其余背景报文保持正常周期发送
        """
        if not self.current_rcv_node_id:
            logger.warning("当前没有正在运行的背景报文。")
            return

        # 统一转成整型比较 ID
        target_id_int = int(target_node_id, 16) if isinstance(target_node_id, str) else int(target_node_id)

        # 过滤掉要停发的节点
        remaining_nodes = [
            node for node in self.current_rcv_node_id
            if (int(node.get("nodeID"), 16) if isinstance(node.get("nodeID"), str) else int(
                node.get("nodeID"))) != target_id_int
        ]

        logger.info(f"🚫 [节点控制] 正在停发节点报文: 0x{target_id_int:03X}...")

        # 将更新后的节点列表重新配置进 ZLG 硬件（会自动清除旧配置并生效新配置）
        self.setup_background_messages(remaining_nodes)

    def clear_background_messages(self):
        """
        清空并关闭硬件上的所有底层周期性背景报文发送
        """
        if self.device_handle != INVALID_DEVICE_HANDLE:
            try:
                self.zcanlib.ZCAN_SetValue(self.device_handle, f"{self.port}/clear_auto_send", b"0")
                self.current_rcv_node_id = None
                logger.info("🚫 [节点控制] 已关闭并清空硬件上的所有底层周期背景报文。")
            except Exception as e:
                logger.warning(f"清空周期背景报文失败: {e}")

    def load_algorithm_dll(self, sa_lib_path):
        """
        根据 saLibPath 加载。
        如果由于相对路径导致文件不存在，将自动在当前代码所在的同级目录下寻找 'VF65ZLGDll.dll' 备用。
        """
        abs_path = None
        if sa_lib_path:
            abs_path = os.path.abspath(sa_lib_path)
            if sys.platform == "win32" and abs_path.endswith(".so"):
                abs_path = abs_path.replace(".so", ".dll")

        # 智能备用路径寻址
        if not abs_path or not os.path.exists(abs_path):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            fallback_path = os.path.join(script_dir, "VF65ZLGDll.dll")
            if os.path.exists(fallback_path):
                logger.info(f"指定的算法库未找到，已自动切换至同级目录备用路径: {fallback_path}")
                abs_path = fallback_path
            else:
                logger.warning(f"算法库文件不存在，且同级备用路径 {fallback_path} 亦存在。")
                return

        try:
            if sys.version_info >= (3, 8):
                os.add_dll_directory(os.path.dirname(abs_path))
            self.dll = ctypes.CDLL(abs_path)
            logger.info(f"安全算法库动态加载成功: {abs_path}")
        except Exception as e:
            logger.error(f"安全算法库动态加载失败: {e}，路径为: {abs_path}")
            self.dll = None

    def connect(self, baud_rate=500):
        logger.info("正在打开 ZLG CAN 设备...")
        self.device_handle = self.zcanlib.OpenDevice(self.device_type, 0, 0)
        if self.device_handle == INVALID_DEVICE_HANDLE:
            raise RuntimeError("打开 ZLG 硬件设备失败！")

        baud_rate_bps = str(int(baud_rate) * 1000).encode('utf-8')
        self.zcanlib.ZCAN_SetValue(self.device_handle, f"{self.port}/canfd_abit_baud_rate", baud_rate_bps)
        self.zcanlib.ZCAN_SetValue(self.device_handle, f"{self.port}/initenal_resistance", b"1")

        chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
        chn_init_cfg.can_type = ZCAN_TYPE_CANFD
        chn_init_cfg.config.canfd.mode = 0
        self.chn_handle = self.zcanlib.InitCAN(self.device_handle, self.port, chn_init_cfg)

        if self.zcanlib.StartCAN(self.chn_handle) != ZCAN_STATUS_OK:
            raise RuntimeError("启动 CAN 通道失败！")

        self.thread_flag = True
        self.rx_thread = threading.Thread(target=self._recv_loop)
        self.rx_thread.daemon = True
        self.rx_thread.start()
        logger.info("ZLG CAN 连接成功，接收线程已启动。")

    def disconnect(self):
        self.thread_flag = False
        if self.rx_thread:
            self.rx_thread.join(timeout=1.0)

        # 退出前彻底清除周期自动发送，防止设备断开后硬件依旧盲发背景报文
        if self.device_handle != INVALID_DEVICE_HANDLE:
            try:
                self.zcanlib.ZCAN_SetValue(self.device_handle, f"{self.port}/clear_auto_send", b"0")
                logger.info("硬件自动周期发送已清空。")
            except Exception as e:
                logger.warning(f"清空自动周期发送失败: {e}")

        if self.chn_handle != INVALID_CHANNEL_HANDLE:
            self.zcanlib.ResetCAN(self.chn_handle)
        if self.device_handle != INVALID_DEVICE_HANDLE:
            self.zcanlib.CloseDevice(self.device_handle)
        logger.info("ZLG CAN 设备已关闭。")

    def _recv_loop(self):
        while self.thread_flag:
            has_data = False

            # 1. 读取普通 CAN 缓冲区
            rcv_num_can = self.zcanlib.GetReceiveNum(self.chn_handle, ZCAN_TYPE_CAN)
            if rcv_num_can > 0:
                has_data = True
                rcv_msg, actual_num = self.zcanlib.Receive(self.chn_handle, min(rcv_num_can, 500), 0)
                for i in range(actual_num):
                    frame = rcv_msg[i].frame
                    can_id = frame.can_id & 0x1FFFFFFF
                    if can_id == self.resp_id:
                        self.rx_queue.put([frame.data[j] for j in range(frame.can_dlc)])

            # 2. 读取 CANFD 缓冲区（USBCANFD-200U 必须读取此缓冲区！）
            rcv_num_fd = self.zcanlib.GetReceiveNum(self.chn_handle, ZCAN_TYPE_CANFD)
            if rcv_num_fd > 0:
                has_data = True
                rcv_msg_fd, actual_num_fd = self.zcanlib.ReceiveFD(self.chn_handle, min(rcv_num_fd, 500), 0)
                for i in range(actual_num_fd):
                    frame = rcv_msg_fd[i].frame
                    can_id = frame.can_id & 0x1FFFFFFF
                    if can_id == self.resp_id:
                        self.rx_queue.put([frame.data[j] for j in range(frame.can_dlc)])

            # 只有两个缓冲区都没有数据时才休眠 1ms
            if not has_data:
                time.sleep(0.001)

    def call_zlgkey(self, seed_array, security_level, variant):
        """
        密钥计算函数：已经无缝接入底层算法动态库 (VF65ZLGDll.dll)
        """
        if self.dll is None:
            raise RuntimeError("算法库未成功加载！")

        seed_array_c = (ctypes.c_ubyte * len(seed_array))(*seed_array)
        seed_array_size = ctypes.c_ushort(len(seed_array))
        security_level_c = ctypes.c_uint(security_level)
        variant_c = ctypes.c_char_p(variant.encode('utf-8'))

        key_array = (ctypes.c_ubyte * 16)()
        key_array_size = ctypes.c_ushort(16)

        result = self.dll.ZLGKey(
            seed_array_c, seed_array_size, security_level_c, variant_c,
            key_array, ctypes.byref(key_array_size)
        )

        if result != 0:
            raise RuntimeError(f"ZLGKey failed with error code: {result}")

        return list(key_array[:key_array_size.value])

    def send_uds_raw(self, payload_8bytes, can_id=None):
        if can_id is None:
            can_id = self.req_id

        msgs = (ZCAN_Transmit_Data * 1)()
        msgs[0].transmit_type = 0
        msgs[0].frame.can_id = can_id
        msgs[0].frame.can_dlc = 8
        for i in range(8):
            msgs[0].frame.data[i] = payload_8bytes[i]
        tx_hex = " ".join([f"{b:02X}" for b in payload_8bytes])
        logger.info(f"[TX] ID: 0x{can_id:03X} | DATA: {tx_hex}")
        return self.zcanlib.Transmit(self.chn_handle, msgs, 1) == 1

    def wait_uds_response(self, timeout=2.0):
        try:
            data = self.rx_queue.get(timeout=timeout)
            rx_hex = " ".join([f"{b:02X}" for b in data])
            logger.info(f"[RX] ID: 0x{self.resp_id:03X} | DATA: {rx_hex}")
            return data
        except queue.Empty:
            return None

    def execute_service(self, service_name, payload, addressing="physical", timeout=3.0):
        logger.info(f"=== [执行诊断服务] {service_name} ({addressing}) ===")
        payload = list(payload)
        while not self.rx_queue.empty():
            try:
                self.rx_queue.get_nowait()
            except queue.Empty:
                break

        # 动态绑定寻址发送 ID
        target_id = self.req_id if addressing == "physical" else self.func_id

        # 智能判定是否为多帧发送请求
        is_multi_frame_send = len(payload) > 2 and (payload[0] >> 4) == 0x1

        if is_multi_frame_send:
            # 提取首帧中指示的实际期望总诊断数据长度并进行 0xFF 补齐
            tp_total_len = ((payload[0] & 0x0F) << 8) | payload[1]
            expected_payload_len = tp_total_len + 2

            if len(payload) < expected_payload_len:
                pad_count = expected_payload_len - len(payload)
                logger.info(
                    f"检测到多帧载荷长度不足。首帧头部指示长度为: {tp_total_len} 字节（期待整包 {expected_payload_len} 字节），"
                    f"实际长度为: {len(payload)} 字节。自动在尾部追加 {pad_count} 个 0xFF 补齐。"
                )
                payload.extend([0xFF] * pad_count)

        if not is_multi_frame_send:
            # 1. 单帧发送分支
            can_data = list(payload)
            while len(can_data) < 8:
                can_data.append(0x00)
            if not self.send_uds_raw(can_data, can_id=target_id):
                return None
        else:
            # 2. 多帧发送分支 (带流控制解析与连续帧延时)
            logger.info("多帧首帧发送就绪，启动流控制 (Flow Control) 与连续帧 (CF) 机制")

            # 发送首帧（取前 8 字节）
            first_frame = payload[:8]
            if not self.send_uds_raw(first_frame, can_id=target_id):
                return None

            # 等待接收端（ECU）回复流控制帧 (FC)
            fc = self.wait_uds_response(timeout)
            if not fc or (fc[0] >> 4) != 3:
                logger.error("未收到接收端回复的流控制帧(FC)！多帧发送终止。")
                return None

            # 解析流控制状态 Flow Status
            flow_status = fc[0] & 0x0F
            if flow_status != 0:
                logger.error(f"流控制状态异常(Flow Status={flow_status})，停止发送后续帧")
                return None

            # 解析 STmin 帧最小间隔时间
            st_min_byte = fc[2]
            if st_min_byte <= 0x7F:
                st_min_ms = st_min_byte
            elif 0xF1 <= st_min_byte <= 0xF9:
                st_min_ms = (st_min_byte & 0x0F) * 0.1  # 100us = 0.1ms
            else:
                st_min_ms = 10  # 默认 10ms

            logger.info(f"收到有效的流控制帧 (FC) | ECU 要求发送间隔 STmin: {st_min_ms}ms")

            # 提取剩余数据并开始循环发送连续帧 (CF)
            remain_payload = payload[8:]
            seq = 1  # 连续帧序号从 1 开始

            while remain_payload:
                # 遵从 ECU 要求的 STmin 帧间隔延时
                time.sleep(st_min_ms / 1000.0)

                # 取出最多 7 字节净荷
                chunk = remain_payload[:7]
                remain_payload = remain_payload[7:]

                # 拼装连续帧 (CF)
                cf_data = [0x20 | seq] + chunk
                while len(cf_data) < 8:
                    cf_data.append(0x00)

                # 发送连续帧
                if not self.send_uds_raw(cf_data, can_id=target_id):
                    return None

                seq = (seq + 1) & 0x0F

        # 3. 接收响应判定 (多帧/单帧公用)
        start_time = time.time()
        while True:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                logger.info("未收到响应（满足超时退出或预期无响应状态）")
                return None

            resp = self.wait_uds_response(max(0.01, timeout - elapsed))
            if not resp:
                return None

            # 处理 Pending 响应 (NRC 78)
            sid = payload[2] if (is_multi_frame_send and len(payload) > 2) else (payload[1] if len(payload) > 1 else None)
            if len(resp) >= 4 and resp[1] == 0x7F and sid is not None and resp[2] == sid and resp[3] == 0x78:
                logger.info("收到 NRC 78 (Pending)，继续等待...")
                start_time = time.time()  # 重置起始时间
                continue

            # === 核心修改：接收端多帧判定与流控制自动回复、重构组包机制 ===
            pci = resp[0] >> 4
            if pci == 0x1:  # 0x1 代表收到首帧 (First Frame)
                logger.info("收到 ECU 回复的多帧首帧 (FF)，正在向其回复流控制帧 (FC) 并执行多帧数据拼接...")

                # 1. 立即向其回复流控制帧 (使用物理 req_id 发送，通知 ECU 允许发送连续帧)
                fc_payload = [0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
                self.send_uds_raw(fc_payload, can_id=self.req_id)

                # 2. 循环读取并拼接后续所有的连续帧 (CF)
                total_len = ((resp[0] & 0x0F) << 8) | resp[1]

                # 提取首帧数据净荷部分 (第 2 字节开始是 UDS 载荷)
                assembled_payload = list(resp[2:])
                received_len = 6  # 首帧中已包含了 6 字节的数据净荷

                start_time_cf = time.time()
                while received_len < total_len and (time.time() - start_time_cf < 5.0):
                    cont_frame = self.wait_uds_response(0.1)  # 快速等待连续帧
                    if not cont_frame:
                        continue
                    if (cont_frame[0] >> 4) == 0x2:  # 判定为连续帧 (CF)
                        assembled_payload.extend(cont_frame[1:])
                        received_len += 7  # 加上当前连续帧的 7 字节

                logger.info("多帧背景数据拼接接收完毕。正在重构整包数据流...")

                # 3. 核心修改：将“首帧的2字节头部（如 10 14）”与“组包拼接好的载荷（assembled_payload）”进行完全重构连接
                # 这样可以还原出一整条完整的、长达 22 字节的长数据流，能够完美支持任意长度（哪怕超过 8 字节）的预期断言比对
                full_reconstructed_stream = list(resp[:2]) + list(assembled_payload[:total_len])

                return_obj = HexList(full_reconstructed_stream)
                # 附加属性：保存剥离了协议控制头的纯净 UDS 诊断数据（供 27 安全算法的 Seed 提取继续使用）
                return_obj.assembled_data = HexList(assembled_payload[:total_len])
                return return_obj

            return HexList(resp)

    def setup_background_messages(self, rcv_node_id_list):
        """
        底层周期背景报文配置
        """
        if self.current_rcv_node_id == rcv_node_id_list:
            logger.info("底层周期背景报文已在发送中且配置未变，保持无缝持续发送（跳过重置清理）。")
            return

        # 仅在初次启动、或进入不同套件发生报文变化时执行清理重置
        logger.info("周期背景报文配置发生变化（或首次启动），执行重置初始化...")
        self.zcanlib.ZCAN_SetValue(self.device_handle, f"{self.port}/clear_auto_send", b"0")

        for i, msg in enumerate(rcv_node_id_list):
            node_id_str = msg.get("nodeID")
            node_id = int(node_id_str, 16) if isinstance(node_id_str, str) else int(node_id_str)

            period = int(msg.get("period", 10))

            raw_data = msg.get("data", [])
            data_bytes = [int(x, 16) if isinstance(x, str) else int(x) for x in raw_data]

            auto_can = ZCAN_AUTO_TRANSMIT_OBJ()
            memset(addressof(auto_can), 0, sizeof(auto_can))
            auto_can.index = i
            auto_can.enable = 1
            auto_can.interval = period
            auto_can.obj.transmit_type = 0
            auto_can.obj.frame.can_id = node_id
            auto_can.obj.frame.can_dlc = len(data_bytes)
            for j in range(auto_can.obj.frame.can_dlc):
                auto_can.obj.frame.data[j] = data_bytes[j]
            self.zcanlib.ZCAN_SetValue(self.device_handle, f"{self.port}/auto_send", byref(auto_can))

        self.zcanlib.ZCAN_SetValue(self.device_handle, f"{self.port}/apply_auto_send", b"0")

        # 缓存当前的配置
        self.current_rcv_node_id = rcv_node_id_list
        logger.info(f"底层周期性仿真背景报文已激活，共启用了 {len(rcv_node_id_list)} 个节点。")