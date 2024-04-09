import functools
import itertools
from typing import (
    Dict,
    Iterable,
    Sequence,
    Tuple,
    Type,
    cast,
)

from eth_hash.auto import (
    keccak,
)
from eth_typing import (
    BlockNumber,
    Hash32,
)
from eth_utils import (
    encode_hex,
)
from trie.exceptions import (
    MissingTrieNode,
)

from xvm._warnings import (
    catch_and_ignore_import_warning,
)
from xvm.abc import (
    AtomicDatabaseAPI,
    BlockAPI,
    BlockHeaderAPI,
    ChainDatabaseAPI,
    DatabaseAPI,
    ReceiptAPI,
    ReceiptDecoderAPI,
    SignedTransactionAPI,
    TransactionDecoderAPI,
)
from xvm.constants import (
    EMPTY_UNCLE_HASH,
    GENESIS_PARENT_HASH,
)
from xvm.db.chain_gaps import (
    GENESIS_CHAIN_GAPS,
    GapChange,
    GapInfo,
    fill_gap,
    is_block_number_in_gap,
    reopen_gap,
)
from xvm.db.header import (
    HeaderDB,
)
from xvm.db.schema import (
    SchemaV1,
)
from xvm.db.trie import (
    make_trie_root_and_nodes,
)
from xvm.exceptions import (
    HeaderNotFound,
    ReceiptNotFound,
    TransactionNotFound,
)
from xvm.rlp.sedes import (
    chain_gaps,
)
from xvm.typing import (
    ChainGaps,
)
from xvm.validation import (
    validate_word,
)
from xvm.vm.header import (
    HeaderSedes,
)

with catch_and_ignore_import_warning():
    from eth_utils import (
        ValidationError,
        to_tuple,
    )
    import rlp
    from trie import (
        HexaryTrie,
    )


class BlockDataKey(rlp.Serializable):
    # used for transactions and withdrawals
    fields = [
        ("block_number", rlp.sedes.big_endian_int),
        ("index", rlp.sedes.big_endian_int),
    ]
