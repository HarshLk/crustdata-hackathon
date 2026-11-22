# Case 16: lookalike company

**Rule:** D-27 / DESIGN 5.7 strict identity: clients match only on the exact normalised legal name or a person-confirmed
alias. "Alpha Demo Agro Foods Pvt Ltd" is neither the legal name nor an alias of Client A ("Alpha Demo Agro Industries Pvt Ltd"); Crustdata identify may be
used to show why the names differ, never to resolve them. Result: cannot verify AMBIGUOUS_MATCH, naming the legal
entity with no agreement (the PRD allows AMBIGUOUS_MATCH or TERMS_MISSING; the gate returns AMBIGUOUS_MATCH).
The deduction's `client_id` is the normalised raw name, as d1's reader sets it for an unmatched client.

**Arithmetic:** none; disputable 0. At most one identify call (`max_paid_calls: 1`).
