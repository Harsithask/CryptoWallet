"""
main.py — Blockchain core with:
  Feature 3: Nakamoto Consensus (longest valid chain)
  Feature 4: 51% Attack simulation (real fork + PoW race) + Invalid block rejection
  Feature 5: Real performance metrics (block time, TPS, network delay, scalability)
"""

import hashlib
import json
import os
import time as time_module
import random
from time import time
from copy import deepcopy


# ─────────────────────────────────────────────────────────
# BLOCK
# ─────────────────────────────────────────────────────────

class Block:
    def __init__(self, index, transactions, timestamp, previous_hash, nonce=0):
        self.index = index
        self.transactions = transactions
        self.timestamp = timestamp
        self.previous_hash = previous_hash
        self.nonce = nonce
        self.hash = self.compute_hash()

    def compute_hash(self):
        block_string = json.dumps({
            "index": self.index,
            "transactions": self.transactions,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "nonce": self.nonce
        }, sort_keys=True)
        return hashlib.sha256(block_string.encode()).hexdigest()

    def to_dict(self):
        return vars(self)


# ─────────────────────────────────────────────────────────
# BLOCKCHAIN NODE
# ─────────────────────────────────────────────────────────

class Blockchain:
    def __init__(self, node_id, difficulty=3):
        self.node_id = node_id
        self.difficulty = difficulty
        self.unconfirmed_transactions = []
        self.chain = []
        self.storage_file = f"chain_{node_id}.json"
        self._load_or_create()

    # ── persistence ──────────────────────────────────────

    def _load_or_create(self):
        if os.path.exists(self.storage_file):
            with open(self.storage_file) as f:
                data = json.load(f)
            self.chain = []
            for b in data:
                block = Block(b["index"], b["transactions"],
                              b["timestamp"], b["previous_hash"], b["nonce"])
                block.hash = b["hash"]
                self.chain.append(block)
        else:
            self._create_genesis()
            self._save()

    def _save(self):
        if self.storage_file:
            with open(self.storage_file, "w") as f:
                json.dump([b.to_dict() for b in self.chain], f, indent=2)

    def _create_genesis(self):
        g = Block(0, [], time(), "0" * 64)
        self.chain.append(g)

    # ── properties ───────────────────────────────────────

    @property
    def last_block(self):
        return self.chain[-1]

    def chain_as_dicts(self):
        return [b.to_dict() for b in self.chain]

    # ── transactions ─────────────────────────────────────

    def add_transaction(self, sender, recipient, amount):
        tx = {"from": sender, "to": recipient,
              "amount": amount, "timestamp": time()}
        self.unconfirmed_transactions.append(tx)
        return tx

    # ── proof of work ────────────────────────────────────

    def proof_of_work(self, block):
        block.nonce = 0
        h = block.compute_hash()
        while not h.startswith("0" * self.difficulty):
            block.nonce += 1
            h = block.compute_hash()
        return h

    def is_valid_proof(self, block, block_hash):
        return (block_hash.startswith("0" * self.difficulty) and
                block_hash == block.compute_hash())

    # ── add a received block (with full validation) ──────

    def add_block(self, block_data: dict):
        b = Block(block_data["index"], block_data["transactions"],
                  block_data["timestamp"], block_data["previous_hash"],
                  block_data["nonce"])
        claimed_hash = block_data["hash"]

        if b.previous_hash != self.last_block.hash:
            return False, (
                f"REJECTED — previous_hash mismatch. "
                f"Expected: {self.last_block.hash[:16]}... "
                f"Got: {b.previous_hash[:16]}..."
            )
        if not self.is_valid_proof(b, claimed_hash):
            return False, (
                f"REJECTED — PoW invalid. "
                f"Hash {claimed_hash[:16]}... does not meet difficulty={self.difficulty}"
            )

        b.hash = claimed_hash
        self.chain.append(b)
        self._save()
        return True, "ACCEPTED"

    # ── mine ─────────────────────────────────────────────

    def mine(self):
        if not self.unconfirmed_transactions:
            return None, 0

        new_block = Block(
            index=self.last_block.index + 1,
            transactions=self.unconfirmed_transactions,
            timestamp=time(),
            previous_hash=self.last_block.hash
        )

        t0 = time_module.perf_counter()
        proof = self.proof_of_work(new_block)
        elapsed = time_module.perf_counter() - t0

        new_block.hash = proof
        self.chain.append(new_block)
        self.unconfirmed_transactions = []
        self._save()
        return new_block, elapsed

    # ─────────────────────────────────────────────────────
    # FEATURE 3 — NAKAMOTO CONSENSUS
    # ─────────────────────────────────────────────────────

    def validate_chain(self, chain_dicts: list):
        """
        Fully validates a chain (list of block dicts).
        Returns (valid: bool, log: list[str])
        """
        log = []
        if not chain_dicts:
            return False, ["Empty chain"]

        chain = []
        for d in chain_dicts:
            b = Block(d["index"], d["transactions"],
                      d["timestamp"], d["previous_hash"], d["nonce"])
            b.hash = d["hash"]
            chain.append(b)

        if chain[0].hash != self.chain[0].hash:
            log.append(f"  Block 0: genesis hash mismatch — this peer is on a different blockchain")
            log.append(f"  Our genesis  : {self.chain[0].hash[:32]}...")
            log.append(f"  Peer genesis : {chain[0].hash[:32]}...")
            return False, log
        log.append(f"  Block 0 [genesis]: OK — hash matches")

        # Detect divergence point
        for i in range(min(len(self.chain), len(chain))):
            if self.chain[i].hash != chain[i].hash:
                log.append(f"  Chains diverge at block {i} — peer took a different fork from our block {i-1}")
                log.append(f"  Our  block {i}: {self.chain[i].hash[:24]}...")
                log.append(f"  Peer block {i}: {chain[i].hash[:24]}...")
                log.append(f"  Fix: use /nodes/sync to align all nodes before mining on them")
                return False, log

        for i in range(1, len(chain)):
            curr = chain[i]
            prev = chain[i - 1]

            if curr.previous_hash != prev.hash:
                log.append(
                    f"  Block {i}: FAIL previous_hash broken "
                    f"(expected {prev.hash[:12]}... got {curr.previous_hash[:12]}...)"
                )
                return False, log

            recomputed = curr.compute_hash()
            if recomputed != curr.hash:
                log.append(
                    f"  Block {i}: FAIL hash mismatch — block data tampered "
                    f"(stored {curr.hash[:12]}... recomputed {recomputed[:12]}...)"
                )
                return False, log

            if not curr.hash.startswith("0" * self.difficulty):
                log.append(
                    f"  Block {i}: FAIL PoW not satisfied "
                    f"(hash {curr.hash[:12]}... needs {self.difficulty} leading zeros)"
                )
                return False, log

            log.append(
                f"  Block {i}: OK | linked | hash valid | PoW ok | "
                f"txs={len(curr.transactions)} | nonce={curr.nonce}"
            )

        return True, log

    def resolve_conflicts(self, peer_chains: list):
        """
        Nakamoto consensus: adopt the longest structurally valid chain.
        peer_chains: list of (node_id, chain_as_dicts)
        Returns detailed report dict.
        """
        our_len = len(self.chain)
        best_len = our_len
        best_chain = None
        best_node = None
        report = {
            "our_node": self.node_id,
            "our_length": our_len,
            "peers_evaluated": [],
            "replaced": False,
            "winner": self.node_id,
        }

        for (peer_id, peer_chain_dicts) in peer_chains:
            peer_len = len(peer_chain_dicts)
            entry = {
                "node_id": peer_id,
                "length": peer_len,
                "validation_log": [],
                "outcome": "",
            }

            if peer_len <= best_len:
                entry["outcome"] = f"SKIPPED — length {peer_len} <= current best {best_len}"
                report["peers_evaluated"].append(entry)
                continue

            valid, log = self.validate_chain(peer_chain_dicts)
            entry["validation_log"] = log

            if not valid:
                entry["outcome"] = "REJECTED — chain failed validation"
            else:
                entry["outcome"] = f"ACCEPTED — new best (length {peer_len})"
                best_len = peer_len
                best_chain = peer_chain_dicts
                best_node = peer_id

            report["peers_evaluated"].append(entry)

        if best_chain is not None:
            self.chain = []
            for d in best_chain:
                b = Block(d["index"], d["transactions"],
                          d["timestamp"], d["previous_hash"], d["nonce"])
                b.hash = d["hash"]
                self.chain.append(b)
            self._save()
            report["replaced"] = True
            report["winner"] = best_node
            report["new_length"] = best_len

        return report


# ─────────────────────────────────────────────────────────
# FEATURE 4 — ATTACK SIMULATION
# ─────────────────────────────────────────────────────────

def _ephemeral_chain(difficulty, seed_chain=None):
    """Create an in-memory Blockchain with no file I/O."""
    bc = Blockchain.__new__(Blockchain)
    bc.node_id = "ephemeral"
    bc.difficulty = difficulty
    bc.unconfirmed_transactions = []
    bc.storage_file = None
    bc.chain = deepcopy(seed_chain) if seed_chain else []
    if not bc.chain:
        bc._create_genesis()
    return bc


class AttackSimulator:

    def __init__(self, honest_chain: Blockchain):
        self.honest = honest_chain

    # ── 51% Attack ───────────────────────────────────────

    def simulate_51_percent_attack(self, attacker_hash_pct: int = 60, fork_depth: int = 2):
        """
        Realistic 51% attack:
        1. Attacker secretly forks from (chain_tip - fork_depth).
        2. Both sides mine simultaneously; hash-power ratio determines speed.
        3. Attacker broadcasts fork; accepted if longer AND internally valid.

        NOTE: A forked chain is valid if every block within it is internally
        consistent (hash linkage + PoW). It does NOT need to share history
        with the honest chain beyond the fork point — that is the whole point
        of a fork. The only rule is: longest internally-valid chain wins.
        """
        honest_pct = 100 - attacker_hash_pct
        log = []
        log.append("=== 51% ATTACK SIMULATION ===")
        log.append(f"Attacker hash power : {attacker_hash_pct}%")
        log.append(f"Honest   hash power : {honest_pct}%")

        honest_snapshot = deepcopy(self.honest.chain)
        chain_len = len(self.honest.chain)
        fork_depth = min(fork_depth, chain_len - 1)
        fork_point = chain_len - fork_depth

        log.append(f"\nHonest chain length : {chain_len} blocks")
        log.append(f"Fork point          : block #{fork_point - 1} "
                   f"(last shared block, both chains agree up to here)")
        log.append(f"Blocks attacker must override: {fork_depth}")
        log.append(f"To win, attacker chain must be longer than {chain_len}")

        # --- Attacker's private fork ---
        # Starts from the shared prefix (blocks 0..fork_point-1), then mines
        # its own blocks from there — these will have different hashes than
        # the honest chain's blocks at the same indices.
        attacker_bc = _ephemeral_chain(self.honest.difficulty,
                                       self.honest.chain[:fork_point])

        log.append(f"\n--- PHASE 1: Attacker mines private fork (secretly) ---")
        log.append(f"  Starting from shared block #{fork_point - 1}, "
                   f"attacker mines its own chain with fraudulent transactions.")

        # Attacker needs enough blocks so its total chain length > honest chain length
        # after the honest network also mines during this same time window.
        # Honest mines proportionally fewer blocks due to lower hash power.
        # We calculate how many the honest network gets, then ensure attacker beats that.
        # honest_blocks_in_window = round(attacker_blocks * honest_pct / attacker_hash_pct)
        # We want: fork_point + attacker_blocks > chain_len + honest_blocks_in_window
        # Solving: attacker_blocks > (chain_len - fork_point) * attacker_hash_pct / honest_pct
        # Use fork_depth * attacker_pct / honest_pct + 2 as safe ceiling
        # How many blocks must the attacker mine?
        # The honest network mines proportionally during the same window:
        #   honest_blocks = round(atk_blocks * honest_pct / atk_pct)
        # For attacker to win:
        #   fork_point + atk_blocks > fork_point + fork_depth + honest_blocks
        #   atk_blocks - round(atk_blocks * honest_pct / atk_pct) > fork_depth
        # We iterate upward from fork_depth+1 until the condition is satisfied,
        # then add 1 extra block as a strict margin so the attacker is LONGER
        # (not equal) to the honest chain after the race.
        if honest_pct == 0:
            blocks_needed = fork_depth + 2
        elif attacker_hash_pct <= honest_pct:
            # attacker can't guarantee a win — give lots of blocks to try anyway
            blocks_needed = fork_depth * 6 + 2
        else:
            blocks_needed = fork_depth + 1
            while True:
                honest_in_window = round(blocks_needed * honest_pct / attacker_hash_pct)
                # attacker wins if its total > honest total
                # attacker total = fork_point + blocks_needed
                # honest  total  = fork_point + fork_depth + honest_in_window
                if blocks_needed > fork_depth + honest_in_window:
                    blocks_needed += 1  # one extra for strict margin
                    break
                blocks_needed += 1

        atk_times = []
        for i in range(blocks_needed):
            attacker_bc.unconfirmed_transactions = [{
                "from": "ATTACKER_DOUBLE_SPEND",
                "to": "ATTACKER_WALLET",
                "amount": 9999,
                "timestamp": time()
            }]
            blk, elapsed = attacker_bc.mine()
            scaled = round(elapsed * (50.0 / attacker_hash_pct), 6)
            atk_times.append(scaled)
            log.append(
                f"  Attacker block #{blk.index}: nonce={blk.nonce} | "
                f"hash={blk.hash[:16]}... | scaled_time={scaled}s"
            )

        # --- Honest network mines in parallel ---
        log.append(f"\n--- PHASE 2: Honest network mines in parallel ---")
        log.append(f"  With only {honest_pct}% hash power, honest network mines "
                   f"proportionally fewer blocks in the same time window.")
        honest_blocks = max(1, round(blocks_needed * honest_pct / attacker_hash_pct)) if honest_pct > 0 else 0
        honest_working = _ephemeral_chain(self.honest.difficulty, self.honest.chain)
        hn_times = []
        for i in range(honest_blocks):
            honest_working.unconfirmed_transactions = [{
                "from": f"HonestMiner_{i}", "to": f"Recipient_{i}",
                "amount": 10, "timestamp": time()
            }]
            blk, elapsed = honest_working.mine()
            if blk:
                scaled = round(elapsed * (50.0 / honest_pct), 6)
                hn_times.append(scaled)
                log.append(
                    f"  Honest block #{blk.index}: nonce={blk.nonce} | "
                    f"hash={blk.hash[:16]}... | scaled_time={scaled}s"
                )

        atk_len = len(attacker_bc.chain)
        hon_len = len(honest_working.chain)

        log.append(f"\n--- PHASE 3: Attacker broadcasts fork ---")
        log.append(f"  Attacker chain length : {atk_len}")
        log.append(f"  Honest  chain length  : {hon_len}")
        log.append(f"  Attacker chain longer : {atk_len > hon_len}")

        # --- Attack-specific validation ---
        # A forked chain is VALID if it is internally consistent:
        #   - every block links to its predecessor via previous_hash
        #   - every block's hash is correctly computed (no tampering)
        #   - every block satisfies PoW difficulty
        # It does NOT need to match the honest chain's blocks after the fork point.
        # This is different from the consensus validate_chain which rejects forks
        # because it only accepts chains that extend the same history.
        def is_internally_valid(chain_objs, difficulty):
            prefix = "0" * difficulty
            validation = []
            for i in range(1, len(chain_objs)):
                curr = chain_objs[i]
                prev = chain_objs[i - 1]
                if curr.previous_hash != prev.hash:
                    validation.append(f"  Block {i}: FAIL — broken link")
                    return False, validation
                recomputed = curr.compute_hash()
                if recomputed != curr.hash:
                    validation.append(f"  Block {i}: FAIL — hash tampered")
                    return False, validation
                if not curr.hash.startswith(prefix):
                    validation.append(f"  Block {i}: FAIL — PoW not satisfied")
                    return False, validation
                validation.append(f"  Block {i}: OK (nonce={curr.nonce}, hash={curr.hash[:16]}...)")
            return True, validation

        atk_valid, atk_validation = is_internally_valid(attacker_bc.chain, self.honest.difficulty)

        log.append(f"\n--- PHASE 3b: Validate attacker chain internally ---")
        log.append(f"  (Checks: hash linkage + SHA-256 correctness + PoW on every block)")
        for line in atk_validation:
            log.append(line)
        log.append(f"  Attacker chain internally valid: {atk_valid}")

        # Attack succeeds if attacker chain is longer AND internally valid
        attack_succeeded = atk_valid and (atk_len > hon_len)

        log.append(f"\n--- PHASE 4: Consensus Decision ---")
        log.append(f"  Rule: longest INTERNALLY VALID chain wins")
        log.append(f"  Attacker length {atk_len} vs Honest length {hon_len}")
        if attack_succeeded:
            log.append("  ATTACK SUCCEEDED — attacker chain is longer and valid")
            log.append("  The fraudulent double-spend transactions are now canonical.")
            log.append("  Honest network must abandon its last "
                       f"{hon_len - fork_point} blocks.")
        else:
            if not atk_valid:
                log.append("  ATTACK FAILED — attacker chain is internally invalid")
            else:
                log.append("  ATTACK FAILED — honest chain is longer or equal")
                log.append(f"  Honest network ({honest_pct}% power) kept pace despite "
                           f"attacker's {attacker_hash_pct}% hash power advantage.")

        # Restore honest chain
        self.honest.chain = honest_snapshot
        if self.honest.storage_file:
            self.honest._save()

        return {
            "attacker_hash_pct": attacker_hash_pct,
            "honest_hash_pct": honest_pct,
            "fork_depth": fork_depth,
            "honest_chain_length_before": chain_len,
            "attacker_chain_length": atk_len,
            "honest_chain_length_after_race": len(honest_working.chain),
            "attack_succeeded": attack_succeeded,
            "avg_attacker_block_time_sec": round(sum(atk_times) / len(atk_times), 4) if atk_times else 0,
            "avg_honest_block_time_sec": round(sum(hn_times) / len(hn_times), 4) if hn_times else 0,
            "step_log": log,
        }

    # ── Invalid Block Rejection ───────────────────────────

    def simulate_invalid_block_rejection(self):
        results = []
        target = _ephemeral_chain(self.honest.difficulty, self.honest.chain)

        # Test 1: Wrong previous_hash
        b1 = Block(
            index=target.last_block.index + 1,
            transactions=[{"from": "Attacker", "to": "Attacker", "amount": 9999, "timestamp": time()}],
            timestamp=time(),
            previous_hash="deadbeef" * 8
        )
        target.proof_of_work(b1)
        ok, reason = target.add_block(b1.to_dict())
        results.append({"test": "Wrong previous_hash", "accepted": ok,
                         "reason": reason, "passed": not ok})

        # Test 2: Transaction tampered after mining
        b2 = Block(
            index=target.last_block.index + 1,
            transactions=[{"from": "Alice", "to": "Bob", "amount": 50, "timestamp": time()}],
            timestamp=time(), previous_hash=target.last_block.hash
        )
        target.proof_of_work(b2)
        tampered = b2.to_dict()
        tampered["transactions"][0]["amount"] = 50000   # post-mining tamper
        ok2, reason2 = target.add_block(tampered)
        results.append({"test": "Transaction tampered after mining", "accepted": ok2,
                         "reason": reason2, "passed": not ok2})

        # Test 3: Hash field overwritten
        b3 = Block(
            index=target.last_block.index + 1,
            transactions=[{"from": "C", "to": "D", "amount": 10, "timestamp": time()}],
            timestamp=time(), previous_hash=target.last_block.hash
        )
        target.proof_of_work(b3)
        b3d = b3.to_dict()
        b3d["hash"] = "00" + "f" * 62
        ok3, reason3 = target.add_block(b3d)
        results.append({"test": "Hash field manually overwritten", "accepted": ok3,
                         "reason": reason3, "passed": not ok3})

        # Test 4: Block not mined (nonce=0)
        b4 = Block(
            index=target.last_block.index + 1,
            transactions=[{"from": "E", "to": "F", "amount": 1, "timestamp": time()}],
            timestamp=time(), previous_hash=target.last_block.hash
        )
        ok4, reason4 = target.add_block(b4.to_dict())
        results.append({"test": "Block not mined (nonce=0)", "accepted": ok4,
                         "reason": reason4, "passed": not ok4})

        return {
            "difficulty": target.difficulty,
            "tests_run": len(results),
            "all_passed": all(r["passed"] for r in results),
            "results": results
        }


# ─────────────────────────────────────────────────────────
# FEATURE 5 — PERFORMANCE BENCHMARKING
# ─────────────────────────────────────────────────────────

class PerformanceBenchmark:

    @staticmethod
    def benchmark_block_creation(difficulty=3, num_blocks=5, txs_per_block=3):
        bc = _ephemeral_chain(difficulty)
        results = []
        for i in range(num_blocks):
            for j in range(txs_per_block):
                bc.add_transaction(f"Sender_{i}_{j}", f"Recv_{j}", j + 1)
            blk, elapsed = bc.mine()
            results.append({
                "block_index": blk.index,
                "nonce": blk.nonce,
                "tx_count": len(blk.transactions),
                "time_sec": round(elapsed, 6),
                "hash_prefix": blk.hash[:8]
            })
        return results

    @staticmethod
    def benchmark_throughput(difficulty=3, num_blocks=5, txs_per_block=5):
        bc = _ephemeral_chain(difficulty)
        total_tx = 0
        total_time = 0.0
        for i in range(num_blocks):
            for j in range(txs_per_block):
                bc.add_transaction(f"S{i}{j}", f"R{j}", 1)
            _, elapsed = bc.mine()
            total_tx += txs_per_block
            total_time += elapsed
        return {
            "total_blocks": num_blocks,
            "total_txs": total_tx,
            "total_time_sec": round(total_time, 4),
            "throughput_tps": round(total_tx / total_time, 4) if total_time > 0 else 0
        }

    @staticmethod
    def benchmark_network_delay(num_nodes=5, difficulty=3):
        bc = _ephemeral_chain(difficulty)
        bc.add_transaction("A", "B", 100)
        blk, mine_time = bc.mine()

        node_delays = []
        for n in range(1, num_nodes + 1):
            node = _ephemeral_chain(difficulty, bc.chain[:-1])
            t0 = time_module.perf_counter()
            ok, _ = node.add_block(blk.to_dict())
            val_time = time_module.perf_counter() - t0
            hop_ms = random.uniform(5, 20) * n
            total_ms = val_time * 1000 + hop_ms
            node_delays.append({
                "node": n,
                "validation_ms": round(val_time * 1000, 3),
                "hop_latency_ms": round(hop_ms, 2),
                "total_delay_ms": round(total_ms, 2),
                "accepted": ok
            })

        avg = round(sum(d["total_delay_ms"] for d in node_delays) / len(node_delays), 2)
        return {
            "num_nodes": num_nodes,
            "block_mine_time_sec": round(mine_time, 6),
            "avg_propagation_delay_ms": avg,
            "per_node": node_delays
        }

    @staticmethod
    def benchmark_scalability(max_nodes=8, difficulty=3, txs_per_block=3):
        results = []
        for n in range(1, max_nodes + 1):
            bc = _ephemeral_chain(difficulty)
            for j in range(txs_per_block):
                bc.add_transaction(f"S{n}_{j}", f"R{j}", j + 1)
            blk, mine_time = bc.mine()

            prop_times = []
            for _ in range(n):
                receiver = _ephemeral_chain(difficulty, bc.chain[:-1])
                t0 = time_module.perf_counter()
                receiver.add_block(blk.to_dict())
                prop_times.append(time_module.perf_counter() - t0)

            max_prop = max(prop_times) if prop_times else 0
            results.append({
                "node_count": n,
                "mine_time_sec": round(mine_time, 6),
                "max_propagation_sec": round(max_prop, 6),
                "total_confirmation_sec": round(mine_time + max_prop, 6),
                "txs_confirmed": txs_per_block,
                "nonce": blk.nonce
            })
        return results