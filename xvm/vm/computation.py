import itertools
from types import (
    TracebackType,
)
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    Union,
    cast,
)

from cached_property import (
    cached_property,
)
from eth_typing import (
    Address,
)
from eth_utils import (
    encode_hex,
    get_extended_debug_logger,
)

from xvm._utils.datatypes import (
    Configurable,
)
from xvm._utils.numeric import (
    ceil32,
)
from xvm.abc import (
    CodeStreamAPI,
    ComputationAPI,
    GasMeterAPI,
    MemoryAPI,
    MessageAPI,
    OpcodeAPI,
    StackAPI,
    StateAPI,
    TransactionContextAPI,
)
from xvm.constants import (
    GAS_MEMORY,
    GAS_MEMORY_QUADRATIC_DENOMINATOR,
)
from xvm.exceptions import (
    Halt,
    VMError,
)
from xvm.typing import (
    BytesOrView,
)
from xvm.validation import (
    validate_canonical_address,
    validate_is_bytes,
    validate_uint256,
)
from xvm.vm.code_stream import (
    CodeStream,
)
from xvm.vm.gas_meter import (
    GasMeter,
)
from xvm.vm.logic.invalid import (
    InvalidOpcode,
)
from xvm.vm.memory import (
    Memory,
)
from xvm.vm.message import (
    Message,
)
from xvm.vm.stack import (
    Stack,
)


def NO_RESULT(computation: ComputationAPI) -> None:
    """
    This is a special method intended for usage as the "no precompile found" result.
    The type signature is designed to match the other precompiles.
    """
    raise Exception("This method is never intended to be executed")


def memory_gas_cost(size_in_bytes: int) -> int:
    size_in_words = ceil32(size_in_bytes) // 32
    linear_cost = size_in_words * GAS_MEMORY
    quadratic_cost = size_in_words**2 // GAS_MEMORY_QUADRATIC_DENOMINATOR

    total_cost = linear_cost + quadratic_cost
    return total_cost