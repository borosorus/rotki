# Frankencoin V2 lending

This package is an inactive implementation skeleton. Frankencoin lending is deployed only on
Ethereum; this package targets V2. Do not register the decoder or balance class until their TODOs and
fixtures are complete.

## Protocol model

Each loan is a `PositionV2` contract. It directly holds one collateral token and records gross zCHF
debt (`minted`), a liquidation price, a reserve contribution, and an owner. Positions may be original
or clones; clones share an original position's minting limit.

Position addresses are dynamic. Before decoding a position log, require:

```text
zCHF.getPositionParent(position) == MINTING_HUB_V2
```

Load mutable state such as `owner()` at the transaction block, not at `latest`. Only verified identity
and immutable collateral metadata may be cached across blocks.

## Decoder structure

The V2 hub and roller have fixed-address callbacks. Direct position calls are routed by function
selector and event topic. Each operation keeps its own decoder because its transfers have different
meaning; `_get_position_metadata()` only authenticates the position and loads shared state.

| Operation | Required result |
| --- | --- |
| Open original | Collateral deposit plus the separate 1,000 zCHF opening fee |
| Clone / CloneHelper | Collateral deposit and optional mint; hide intermediate ownership and forwarding |
| `mint()` | Usable zCHF as `WITHDRAWAL/GENERATE_DEBT`, plus financed fee as `SPEND/FEE` |
| `repay()` | Actual payer spend as `SPEND/PAYBACK_DEBT`; retain a different owner/payer |
| `adjust()` | Every collateral/debt delta, ordered deposit → mint or repayment → withdrawal |
| `adjustPrice()` | One informational event and no asset movement |
| Collateral withdrawal | `WITHDRAWAL/WITHDRAW_FROM_PROTOCOL`, alternate recipient, and closure if applicable |
| Ownership transfer | One informational event; ignore helper-internal transfers already covered by clone |
| Roller | Source repayment/withdrawal plus target deposit/mint; remove flash-mint plumbing |
| Plain collateral transfer | Deposit enrichment using a verified-position cache |

`MintingUpdate(collateral, price, minted)` contains resulting totals, not deltas. Match and transform
the ERC20 events already decoded by rotki, then use `maybe_reshuffle_events()` where ordering matters.
Create a new event only when a tracked position owner has no tracked transfer endpoint.

### Reserve accounting

Gross debt is not the zCHF received or repaid by the user:

- Minting sends usable zCHF to the target and sends reserve contribution plus fees to the protocol
  reserve. The zCHF `Profit` log identifies the financed fee. Do not expose the reserve mint as a user
  receive.
- Direct repayment spends the user's non-reserve portion; released reserve accounts for the larger
  gross debt decrease.
- `adjust()` repayment first transfers assigned reserve to the owner and then burns gross zCHF from
  them. Collapse those generic receive/burn events into one net repayment.

The position owner, collateral payer, zCHF recipient, repayer, and collateral recipient may differ.
Attribute protocol activity to the tracked owner while preserving other parties in notes or metadata.

### Position metadata

Every lending event must include `position_address` in `extra_data`. Opening/clone events also include
`original_position_address`; roller events include `source_position_address` and
`target_position_address`. Balance discovery depends on these keys and must not parse notes.

When nested logs describe one operation, decode at the highest level: `PositionOpened` for open/clone
flows and `Roll` for roller flows. Inner `MintingUpdate`, ownership, reserve, mint, burn, and forwarding
events are evidence, not additional user actions.

## Balance representation

History events discover candidate position contracts; current balances come from on-chain queries.
For every candidate:

1. Verify it through the zCHF position registry.
2. Query its current owner and keep it only if that owner is tracked.
3. Query its collateral token and `balanceOf(position)`.
4. Query `minted`, `reserveContribution`, and the currently assigned reserve.

Expose:

```text
assets[collateral][frankencoin] = collateral.balanceOf(position)
liabilities[zCHF][frankencoin] = minted - assigned_reserve
```

The liability is the effective zCHF obligation, not gross debt. This avoids understating net worth by
counting gross debt without the inaccessible reserve that offsets it. Aggregate multiple positions per
owner, skip zero values, tolerate individual bad candidates, and batch state and pricing queries.

## Scope and tests

Liquidations, challenges, forced/expired sales, postponed collateral returns, V1, savings, equity, and
bridges are outside this package.

Before registration, test originals, both clone overloads, CloneHelper price cases, mint recipients,
partial/full and third-party repayment, every `adjust()` direction, price-only updates, both collateral
withdrawal entry points, unrelated-token rescue, direct collateral transfer, closure, ownership change,
roller variants, and multi-action transactions. Reject unrelated contracts with matching topics.

Balance tests must cover multiple positions, ownership transfer, zero debt with collateral, reserve
impairment, collateral decimals, invalid/stale candidates, and asset/liability aggregation.
