# XVM

**XVM** is a high performance execution layer solution based on the Bitcoin network serves as the verification layer of the USBL2 solution.It is smart, high-performance, and inherits the security, decentralization and complete network flaws of the Bitcoin network at the same time.

## Characteristics

- **Compatibility**：Supports the indexing network of smart contracts and supports smart contracts developed by Solidity.
- **Modular blockchain**：Modular design, decomposed into independent modules or layers according to different functions and components in the blockchain, facilitating scalability and customization.
- **Secure**：Using Bitcoin as the data availability layer, XVM transactions are also Bitcoin transactions, inheriting its security, data tamper-proof, and decentralized feature.
- **Low cost**：By integrating the indexer component, off-chain scale is achieved to reduce transaction fees.
- **High efficiency**：Multiple instructions are executed in parallel, improved the network operation efficiency.

## Install

We recommend using docker to deploy XVM.


```
docker-compose up -d
```

## Exposed Ports

| Port | Description                        | External Service |
| ---- | ---------------------------------- | ---------------- |
| 8545 | XVM RPC HTTP Sevice               | ✓                |
| 8679 | Internal RPC Service for 0xvm-core | ✗                |

Since no authentication for internal RPC, **NEVER EXPOSE 8679 PORT TO PUBLIC NETWORK**, make sure the firewall set correctly.

## Simple Test

Simple tests locate in `scripts/tests` directory, you can run it by:

- `scripts/tests/testinternalrpc.py`: Fast and simple test for interaction with internal RPC service.
- `scripts/tests/testrpc.py`: Fast and simple test for interaction with external RPC service.
- `scripts/tests/internal_rpc_test/vbtc.py`: Simple test for deploy a XRC20 token contract.

## Contact us

- Pay a visit of our [Website](https://0xvm.com/)
- Join us in our Discord [chatroom](https://discord.com/invite/Fpwt65wDvU)

