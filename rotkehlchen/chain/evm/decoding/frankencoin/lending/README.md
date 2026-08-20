# Frankencoin V2 lending

This package decodes and values borrower-owned Frankencoin V2 positions on Ethereum. Savings is a
separate Frankencoin module because it has a different contract model and is deployed on more chains.

## Position model

A loan is represented by its own `PositionV2` contract rather than by a transferable position token.
The contract owns one collateral token, records gross zCHF debt and its reserve contribution, and has
one mutable owner. An original position defines minting terms; clones reuse those terms and share the
original position's minting limit.

Position addresses are dynamic and an arbitrary contract can emit lookalike events. Before treating
an address as a position, the integration checks:

```text
zCHF.getPositionParent(position) == MINTING_HUB_V2
```

Historical decoding reads mutable state, especially `owner()`, at the transaction block. Reading it
at `latest` would attribute old actions to a later owner. Immutable collateral metadata can be cached
after the registry check.

## Events shown in history

The decoder covers opening originals, cloning (including CloneHelper), minting, repayment, combined
collateral/debt adjustment, collateral withdrawal, ownership transfer, and rolling debt or collateral
between positions.

The user-facing event sequence describes the economic action rather than every internal ERC20
transfer. Important examples are:

- Minting produces a `WITHDRAWAL/GENERATE_DEBT` for usable zCHF and a separate `SPEND/FEE`. Reserve
  zCHF minted to the protocol is not a user receive.
- Repayment produces `SPEND/PAYBACK_DEBT` for the amount actually funded by the user. Reserve release
  can make the gross debt reduction larger than that spend.
- `adjust()` can combine collateral and debt changes. Only asset movements are shown; changing a
  position parameter without moving collateral or zCHF does not add history noise.
- A roll uses a temporary flash mint internally. The history instead shows the source position's
  repayment/withdrawal and the target position's deposit/mint/fee.
- CloneHelper forwarding and intermediate ownership transfers are folded into the clone operation.

Lifecycle events are decoded only for a tracked position owner. Explicit recipients and collateral
payers during an open or adjustment are retained in notes when they differ from that owner. A
standalone third-party repayment or collateral transfer remains ordinary ERC20 activity instead of
being attributed to the owner.

`MintingUpdate(collateral, price, minted)` reports the resulting totals, not deltas. The decoder uses
those totals together with the transaction's ERC20 transfers and protocol logs. It transforms generic
rotki transfer events where possible so the same movement is never counted twice.

## Discovery and balances

Each lending event stores `position_address` in `extra_data`. Clone/open events also identify the
original position, while rolls identify their source and target. Balance discovery uses these stable
fields; it never parses event notes or assumes a position remains with its historical owner.

On every balance query, candidates discovered from history are re-authenticated and queried for their
current owner, collateral token, collateral balance, gross debt, and reserve contribution. Candidates
that fail independently are skipped, and positions no longer owned by a tracked address are omitted.

Balances are presented as:

```text
assets[collateral][frankencoin] = collateral.balanceOf(position)
liabilities[zCHF][frankencoin] = minted - calculateAssignedReserve(minted, reserveContribution)
```

The liability is the effective zCHF obligation. Gross debt includes protocol-controlled assigned
reserve that the owner cannot use, so showing gross debt without that offset would understate net
worth. Multiple positions and collateral types are aggregated per current owner and priced in batch.

## Scope

This integration is limited to the Ethereum V2 borrower lifecycle. Liquidations, challenges,
forced or expired sales, postponed collateral returns, V1, savings, equity, and bridges are outside
this package.
