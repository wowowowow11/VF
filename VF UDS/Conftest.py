# -*- coding: utf-8 -*-
import pytest
import logging
from UDS_client import ZlgUdsClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d): %(message)s'
)


@pytest.fixture(scope="session")
def uds_client():
    """
    会话级夹具：初始化客户端对象，连接动作延迟到 test 中根据 YAML 的波特率执行
    """
    client = ZlgUdsClient()
    yield client
    client.disconnect()