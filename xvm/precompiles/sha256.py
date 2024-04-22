import hashlib

from xvm import (
    constants,
)
from xvm._utils.numeric import (
    ceil32,
)
from xvm.abc import (
    ComputationAPI,
)


def sha256(computation: ComputationAPI) -> ComputationAPI:
    word_count = ceil32(len(computation.msg.data)) // 32
    gas_fee = constants.GAS_SHA256 + word_count * constants.GAS_SHA256WORD

    computation.consume_gas(gas_fee, reason="SHA256 Precompile")
    input_bytes = computation.msg.data
    hash = hashlib.sha256(input_bytes).digest()
    computation.output = hash
    return computation
