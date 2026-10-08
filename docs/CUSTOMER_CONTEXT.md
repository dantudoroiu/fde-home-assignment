# Customer context and value

The brief leaves the customer open, so this is the scenario I assumed. Every number below is an
assumption, chosen to be realistic for a company of this size.

## The customer
**Brightdesk** is a fictional B2B SaaS company selling a reporting and dashboards platform to
mid-market companies (finance, operations, and sales teams). It has about 2,000 customer workspaces.

## The team
- **Support team:** 12 agents across two time zones, plus 1 support lead.
- **Volume:** about 600 tickets per day, from email and the in-app web form, all landing in one shared
  helpdesk inbox.
- **Commitment:** a 4-hour first-response SLA on business plans, and 1 hour for Enterprise P1 issues.
- **Skills:** agents are non-technical generalists. Billing goes to a billing team, security to the
  security team, and privacy requests to a privacy team.

## How the work is done today
1. Agents pick tickets from a queue sorted by arrival time.
2. They read each ticket, decide the category and urgency, and either reassign it or answer it.
3. They look up the relevant help-center article and write a reply from scratch.

Observed pain (assumed):
- **Triage plus drafting takes 4–6 minutes per ticket.** Most of that time goes on routine questions
  (password resets, invoices, exports) that the help center already answers.
- **Urgent tickets wait behind routine ones.** An outage report or a suspicious-login report can sit
  behind 50 how-to questions, because the queue is first-in, first-out.
- **Quality is inconsistent.** New agents sometimes promise refunds or fix dates they are not allowed to,
  or miss that a ticket is a GDPR request with a legal deadline.

## What the tool does
An assistant inside the agents' existing workflow. It does not replace them. For each incoming ticket:
- **Prioritized queue:** category, priority, sentiment and risk signals are set within seconds of
  arrival, so P1s, security issues and angry customers rise to the top.
- **"Needs review" flags with plain-language reasons**, for example `priority_p1`,
  `sensitive_category:data_privacy`, `mentions_refund_or_commitment` or `low_confidence`.
- **A draft reply grounded in the knowledge base**, with the articles it relied on. The agent edits and
  sends it; nothing is sent automatically.
- **Graceful fallback:** if the AI is down, tickets still arrive and are handled exactly as today.

## Value
- **Time saved:** if drafting drops from about 5 minutes to about 2 minutes for the roughly 60% of
  tickets that are routine, that frees about 18 agent-hours per day, roughly 2 FTE. That capacity can
  absorb growth or go to harder tickets. The model cost is about $4/day (measured $0.007 per ticket at 600 tickets/day, see the README), which is
  negligible next to that.
- **Risk reduction:** P1, security and legal tickets are surfaced immediately instead of in arrival
  order, and risky promises in drafts are flagged before they are sent.
- **Consistency:** replies quote current policy from the knowledge base rather than an agent's memory.

## How success would be measured
| Metric | Source | Target (indicative) |
|---|---|---|
| Median time to first response | helpdesk timestamps | −40% |
| Time to first response for P1 tickets | helpdesk timestamps | under 30 min |
| Draft accepted unchanged / lightly edited | feedback table (`edit_ratio`) | over 50% within 4 weeks |
| Category / priority agreement with agents | feedback corrections | over 85% / over 75% |
| Escalation recall (tickets needing review that were flagged) | weekly audit sample | over 95% |
| AI-unavailable rate | metrics endpoint | under 1% |

Escalation recall is the safety metric: missing an angry P1 is much worse than reviewing one extra
routine ticket, so routing deliberately trades precision for recall.

## Rollout I would propose to the customer
1. **Shadow mode (1–2 weeks):** run the AI on live tickets without showing it to agents. Compare its
   triage with what agents actually did, and tune prompts and thresholds.
2. **Assist mode for 3–4 agents:** show the triage and drafts in the queue. Collect accept, edit and
   reject feedback, and review the metrics weekly with the support lead.
3. **Team-wide assist:** sort the queue by AI priority for everyone.
4. **Later, and only with evidence:** auto-send for a narrow set of ticket types with very high
   acceptance rates, such as password resets. This is explicitly not part of this build.
