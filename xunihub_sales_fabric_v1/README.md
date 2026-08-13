# XuniHub Sales Fabric v1

A compliance-first orchestration core for Almighty Sonoxo promotion and merchandise conversion.

## Goals

- Address 1,000,000 logical workers without spawning one million concurrent processes.
- Keep workers dormant by default and activate bounded batches through the scheduler.
- Route genuine music discovery to `https://soundcloud.com/almightysonoxo/tracks`.
- Route merchandise conversion to `https://direct.distrokid.com/almightysonoxo2/home`.
- Tag external campaigns with UTM attribution.
- Reallocate worker effort only from observable, verified positive signals.
- Count revenue only from authorized, genuine third-party DistroKid Direct order evidence.

## Compliance gate

The core fails closed for artificial SoundCloud activity, fake accounts, auto-follow/unfollow, autoplay used to manufacture streams, artificial likes/comments/reposts/follows, purchased guaranteed engagement, unsolicited bulk DMs, unauthorized scraping, impersonation, fabricated testimonials, self-purchases, platform evasion, and uncontrolled spam.

Workers may prepare compliant research, SEO, campaign copy, landing-page optimization, UTM routing, authorized social content, opt-in email, lawful outreach, ad creative, analytics, A/B experiments, conversion analysis, and merchandising recommendations. External publishing still requires an authorized channel.

## Revenue verification

`VerifiedRevenueLedger` accepts an order only when all of the following hold:

1. provider is `distrokid_direct`;
2. the commerce connection is authorized;
3. status is `paid`;
4. amount is positive;
5. the purchase is a genuine third-party transaction;
6. it is not a self-purchase or test order; and
7. the order ID has not already been counted.

Refunded, chargeback, cancelled/canceled, failed, reversed, and voided evidence does not count. `first_real_dollar_reached` becomes true only after at least 100 verified cents have entered the ledger.

## Measurement boundary

An outbound click to SoundCloud is not a play, follow, repost, or listener. The fabric deliberately does not infer private SoundCloud engagement from external traffic. Those metrics require authorized SoundCloud analytics.

Until an authorized DistroKid Direct order/analytics feed is connected, the readiness state remains `prepare_and_route` and the system must not claim verified merchandise revenue.

## Tests

Run the standard-library suite from the repository root:

`python -m unittest xunihub_sales_fabric_v1.test_fabric`
