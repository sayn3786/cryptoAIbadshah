# TaoVault

A browser-based Bittensor wallet starter built with React, Vite, and Polkadot-compatible wallet extensions.

## Features

- Connects to Bittensor Finney mainnet, testnet, or lite RPC endpoints.
- Uses extension-based signing through `@polkadot/extension-dapp`.
- Displays live free TAO balance from `system.account`.
- Converts account and recipient addresses to Bittensor SS58 format `42`.
- Prepares `balances.transferKeepAlive` transactions without collecting seed phrases or private keys.

## Run Locally

```bash
npm install
npm run dev
```

Then open the local Vite URL shown in the terminal. Install or unlock a Polkadot-compatible browser extension and allow TaoVault to access the account you want to use.

## Safety Notes

TaoVault never asks for a mnemonic. Keep seed phrases offline and only approve transactions after checking the network, destination address, and amount in your wallet extension.

## Useful References

- Bittensor networks: https://docs.learnbittensor.org/concepts/bittensor-networks
- Creating/importing Bittensor wallets: https://docs.learnbittensor.org/keys/working-with-keys
- Bittensor EVM and SS58 conversion notes: https://docs.learnbittensor.org/evm-tutorials/convert-h160-to-ss58
