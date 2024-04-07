from typing import (
    Tuple,
)
from .compression import (
    TMessageBlock,
)

TMessage = Tuple[
    int, int, int, int, int, int, int, int, int, int, int, int, int, int, int, int
]
TFCompressArgs = Tuple[int, TMessageBlock, TMessage, Tuple[int, int], bool]