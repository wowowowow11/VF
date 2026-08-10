import time
import logging
import threading
from zlgcan import *

# 配置基础日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s',
                    datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)


class ZlgUsbcandFd200U:
    def __init__(self, channel=0):
        """
        初始化 CANFD 控制器
        :param channel: CAN通道号 (通常为 0 或 1)
        """
        self.zcanlib = ZCAN()
        self.dev_handle = INVALID_DEVICE_HANDLE
        self.chn_handle = INVALID_CHANNEL_HANDLE
        self.channel = channel

        self.is_running = False
        self.recv_thread = None
        self.print_lock = threading.Lock()

    def connect(self, abit_baud="500000", dbit_baud="2000000", enable_resistor=True):
        """
        打开设备并初始化通道
        :param abit_baud: 仲裁域波特率 (默认 500K)
        :param dbit_baud: 数据域波特率 (默认 2M)
        :param enable_resistor: 是否开启内置 120Ω 终端电阻
        """
        # 1. 打开设备 (设备类型 41 对应 ZCAN_USBCANFD_200U)
        self.dev_handle = self.zcanlib.OpenDevice(ZCAN_USBCANFD_200U, 0, 0)
        if self.dev_handle == INVALID_DEVICE_HANDLE:
            logger.error("打开 USBCANFD-200U 设备失败！请检查连接或占用情况。")
            return False

        # 读取并打印设备信息
        info = self.zcanlib.GetDeviceInf(self.dev_handle)
        if info:
            logger.info(f"成功连接设备，硬件版本: {info.hw_version}, 固件版本: {info.fw_version}, 序列号: {info.serial}")

        # 2. 使用 IProperty 接口设置波特率和电阻 (USBCANFD-200U 必须用此方式)
        try:
            self.zcanlib.ZCAN_SetValue(self.dev_handle, f"{self.channel}/canfd_abit_baud_rate",
                                       abit_baud.encode("utf-8"))
            self.zcanlib.ZCAN_SetValue(self.dev_handle, f"{self.channel}/canfd_dbit_baud_rate",
                                       dbit_baud.encode("utf-8"))
            res_val = "1" if enable_resistor else "0"
            self.zcanlib.ZCAN_SetValue(self.dev_handle, f"{self.channel}/initenal_resistance", res_val.encode("utf-8"))
            # 关闭自发自收回显 (TX Echo)
            self.zcanlib.ZCAN_SetValue(self.dev_handle, f"{self.channel}/set_device_tx_echo", b"0")
        except Exception as e:
            logger.error(f"设置 IProperty 失败: {e}")
            self.close()
            return False

        # 3. 初始化 CAN 通道
        chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
        chn_init_cfg.can_type = ZCAN_TYPE_CANFD
        chn_init_cfg.config.canfd.mode = 0  # 0:正常模式(收发) 1:只听模式

        self.chn_handle = self.zcanlib.InitCAN(self.dev_handle, self.channel, chn_init_cfg)
        if self.chn_handle is None or self.chn_handle == INVALID_CHANNEL_HANDLE:
            logger.error(f"初始化 CAN 通道 {self.channel} 失败！")
            self.close()
            return False

        # 4. 启动 CAN 通道
        if self.zcanlib.StartCAN(self.chn_handle) != ZCAN_STATUS_OK:
            logger.error(f"启动 CAN 通道 {self.channel} 失败！")
            self.close()
            return False

        logger.info(f"CAN 通道 {self.channel} 启动成功！(仲裁 {abit_baud} bps, 数据 {dbit_baud} bps)")

        # 清空启动前可能存在的历史缓存数据
        self.zcanlib.ClearBuffer(self.chn_handle)
        return True

    def start_receive_thread(self):
        """开启后台接收线程"""
        if not self.is_running:
            self.is_running = True
            self.recv_thread = threading.Thread(target=self._receive_task)
            self.recv_thread.daemon = True
            self.recv_thread.start()
            logger.info("后台接收线程已启动。")

    def _receive_task(self):
        """后台接收任务的核心循环"""
        while self.is_running:
            time.sleep(0.005)  # 释放CPU

            # --- 1. 读取经典 CAN 报文 ---
            rcv_num = self.zcanlib.GetReceiveNum(self.chn_handle, ZCAN_TYPE_CAN)
            if rcv_num > 0:
                rcv_msgs, rcv_num = self.zcanlib.Receive(self.chn_handle, min(rcv_num, 100), 50)
                for msg in rcv_msgs[:rcv_num]:
                    self._print_frame(msg.frame, is_fd=False)

            # --- 2. 读取 CANFD 报文 ---
            rcv_canfd_num = self.zcanlib.GetReceiveNum(self.chn_handle, ZCAN_TYPE_CANFD)
            if rcv_canfd_num > 0:
                rcv_canfd_msgs, rcv_canfd_num = self.zcanlib.ReceiveFD(self.chn_handle, min(rcv_canfd_num, 100), 50)
                for msg in rcv_canfd_msgs[:rcv_canfd_num]:
                    self._print_frame(msg.frame, is_fd=True)

    def _print_frame(self, frame, is_fd):
        """格式化打印接收到的 CAN/CANFD 帧"""
        can_id = frame.can_id & 0x1FFFFFFF
        frame_type = "扩展帧" if frame.can_id & (1 << 31) else "标准帧"
        frame_format = "远程帧" if frame.can_id & (1 << 30) else "数据帧"

        if is_fd:
            brs = "加速" if frame.flags & 0x1 else "不加速"
            protocol = f"CANFD({brs})"
            dlc = frame.len
            data_str = " ".join([f"{num:02X}" for num in frame.data[:dlc]])
        else:
            protocol = "经典CAN"
            dlc = frame.can_dlc
            data_str = " ".join([f"{num:02X}" for num in frame.data[:dlc]])

        with self.print_lock:
            logger.info(f"RX | {protocol} | {frame_type} | ID: 0x{can_id:03X} | DLC: {dlc:02d} | Data: {data_str}")

    def send_classic_can(self, can_id, data):
        """
        发送经典 CAN 报文
        :param can_id: 报文 ID (整型)
        :param data: 报文数据列表，如 [0x01, 0x02, 0x03]
        """
        if self.chn_handle == INVALID_CHANNEL_HANDLE: return False

        msgs = (ZCAN_Transmit_Data * 1)()
        msg = msgs[0]
        msg.transmit_type = 0  # 0-正常发送，1-单次发送，2-自发自收，3-单次自发自收
        msg.frame.can_id = can_id
        msg.frame.can_dlc = len(data)

        for i in range(min(len(data), 8)):
            msg.frame.data[i] = data[i]

        ret = self.zcanlib.Transmit(self.chn_handle, msgs, 1)
        if ret == 1:
            data_str = " ".join([f"{x:02X}" for x in data])
            logger.info(f"TX | 经典CAN | ID: 0x{can_id:03X} | DLC: {len(data):02d} | Data: {data_str}")
            return True
        else:
            logger.warning(f"发送失败 ID: 0x{can_id:03X}")
            return False

    def send_canfd(self, can_id, data, brs=True):
        """
        发送 CANFD 报文
        :param can_id: 报文 ID
        :param data: 数据列表 (最大64字节)
        :param brs: 是否开启比特率加速 (默认开启)
        """
        if self.chn_handle == INVALID_CHANNEL_HANDLE: return False

        msgs = (ZCAN_TransmitFD_Data * 1)()
        msg = msgs[0]
        msg.transmit_type = 0
        msg.frame.can_id = can_id
        msg.frame.len = len(data)
        msg.frame.flags = 0x01 if brs else 0x00  # 0x01 表示开启 BRS (加速)

        for i in range(min(len(data), 64)):
            msg.frame.data[i] = data[i]

        ret = self.zcanlib.TransmitFD(self.chn_handle, msgs, 1)
        if ret == 1:
            data_str = " ".join([f"{x:02X}" for x in data])
            logger.info(f"TX | CANFD(加速:{brs}) | ID: 0x{can_id:03X} | DLC: {len(data):02d} | Data: {data_str}")
            return True
        else:
            logger.warning(f"CANFD 发送失败 ID: 0x{can_id:03X}")
            return False

    def close(self):
        """安全释放资源"""
        self.is_running = False
        if self.recv_thread:
            self.recv_thread.join(timeout=2)

        if self.dev_handle != INVALID_DEVICE_HANDLE:
            self.zcanlib.CloseDevice(self.dev_handle)
            self.dev_handle = INVALID_DEVICE_HANDLE
            logger.info("USBCANFD-200U 设备已关闭。")


# ===================== 测试运行 =====================
if __name__ == "__main__":
    # 实例化通道 0
    can0 = ZlgUsbcandFd200U(channel=0)

    # 连接并初始化 (使用 500K/2M 默认波特率)
    if can0.connect(abit_baud="500000", dbit_baud="2000000", enable_resistor=True):
        try:
            # 开启接收线程
            can0.start_receive_thread()

            # 发送测试数据
            print("\n>>> 按 Ctrl+C 退出测试\n")
            while True:
                # 1. 发送一帧经典 CAN
                can0.send_classic_can(can_id=0x123, data=[0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88])
                time.sleep(1)

                # 2. 发送一帧 CANFD (带BRS加速)
                can0.send_canfd(can_id=0x456, data=[0xAA, 0xBB, 0xCC, 0xDD] * 4)  # 发送16字节数据
                time.sleep(2)

        except KeyboardInterrupt:
            logger.info("用户停止程序...")
        finally:
            can0.close()