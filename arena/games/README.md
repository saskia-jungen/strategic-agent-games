# Implemented Games Description: Dictator and Public Project

This doc summarizes the rules and actions for the dictator and public-project games added in this repo.

## Dictator (game_id: dictator)

**Summary**
- Two agents: allocator (agent_ids[0]) and recipient (agent_ids[1]).
- The allocator decides how to split a fixed pie.
- Both agents have private reservation values used to compute utility.

**Phases**
1. negotiation (message_only only)
2. allocate (allocator chooses a split)

**Actions**
- message_only: send messages without advancing turns.
- allocate_split: allocator sets shares.
  - payload: {"allocator_share": number, "recipient_share": number}
  - shares must be >= 0 and sum to the pie.
- pass: allocator skips a turn during allocate.

**Resolution and payoffs**
- When allocate_split is submitted, the game ends immediately.
- Utility: share - reservation_value for each agent.
- If allocation rounds exceed max_rounds, both agents get share 0 and utility -reservation_value.

**Key parameters (defaults)**
- pie: 100
- negotiation_rounds: 2
- max_rounds: 3
- reservation_max: None (uses pie)
- reservation_values: optional fixed values per agent

## Public Project (game_id: public-project)

**Summary**
- N agents privately value a public project with cost C.
- Each agent may report a valuation once; reports can be untruthful.
- If total reported >= C, the project is built and cost is shared equally.
- Payoffs use true valuations.

**Phases**
1. negotiation (round-robin turn order; agents can chat before reporting)
2. report (round-robin turn order)

**Actions**
- message_only: send messages during negotiation (advances the turn).
- pass: skip your turn in report; counts as your final response.
- report_value: submit a non-negative report once (report phase only).
  - payload: {"report": number}

**Resolution and payoffs**
- The game moves from negotiation to report after the negotiation round limit.
- The game ends once every agent has either reported or passed in report, or max rounds are exceeded.
- If built: utility = true_valuation - (project_cost / n).
- If not built: utility = 0.

**Key parameters (defaults)**
- project_cost: 100
- valuation_range: (0, 100)
- valuation_mode: random (or fixed when valuations provided)
- valuations: optional fixed values per agent
- negotiation_rounds: 2
- max_rounds: 10
- cost_sharing: equal

## Trust (game_id: trust)

**Summary**
- Two agents: trustor (agent_ids[0]) and trustee (agent_ids[1]).
- Trustor sends amount x (0 <= x <= endowment). The amount is multiplied by m.
- Trustee returns amount r (0 <= r <= m*x).
- Payoffs: trustor = (endowment - x) + r; trustee = (m*x) - r.

**Phases**
1. negotiation (round-robin turn order; agents can chat before decisions)
2. send (trustor only)
3. return (trustee only)

**Actions**
- message_only: send messages during negotiation (advances the turn).
- send: trustor sends a non-negative amount up to endowment.
  - payload: {"amount": number}
- return_amount: trustee returns a non-negative amount up to multiplier * sent.
  - payload: {"amount": number}
- pass: skip your turn during send/return.

**Resolution and payoffs**
- The game moves from negotiation to send after the negotiation round limit.
- The game ends once the trustee returns, or when send/return round limits are exceeded.
- If timed out: both agents receive utility 0.

**Key parameters (defaults)**
- endowment: 10
- multiplier: 3
- negotiation_rounds: 2
- max_rounds_send: 3
- max_rounds_return: 3
- role_map: optional mapping {trustor: agent_id, trustee: agent_id}


## Test example

Use the following sequence to run the arena, start the dashboard, register two agents, and launch a match:

```bash
# Terminal 1
python run_arena.py --no-browser

# Terminal 2
cd dashboard && npm run dev

# Terminal 3 (register a model)
python agent.py --port 5001 --name "gptoss" --model "openai/gpt-oss-120b:free"

# Terminal 4 (register a model)
python agent.py --port 5002 --name "liquid" --model "liquid/lfm-2.5-1.2b-instruct:free"

# Terminal 5
curl -X POST http://localhost:8888/api/match \
  -H "Content-Type: application/json" \
  -d '{"game_id": "dictator", "agent_ids": ["gptoss", "liquid"]}'

## Trust prompt personalities

Use the built-in prompt variants to test cooperative vs manipulative behavior:

```bash
# Manipulative behavior
python agent_with_builtin_prompts.py --port 5001 --name "gptoss1" --personality manipulative --model "openai/gpt-oss-120b:free"

# Exploitative behavior
python agent_with_builtin_prompts.py --port 5002 --name "gptoss2" --personality exploitative --model "openai/gpt-oss-120b:free"

# Deceptive behavior
python agent_with_builtin_prompts.py --port 5003 --name "gptoss3" --personality deceptive --model "openai/gpt-oss-120b:free"
```
```