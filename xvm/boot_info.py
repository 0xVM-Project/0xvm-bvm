from argparse import Namespace
from typing import Dict, NamedTuple

from xvm.config import XvmConfig


class BootInfo(NamedTuple):
    args: Namespace
    xvm_config: XvmConfig
    profile: bool
    min_log_level: int
    logger_levels: Dict[str, int]
