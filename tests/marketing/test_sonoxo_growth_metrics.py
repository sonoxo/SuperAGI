from superagi.marketing.sonoxo_growth_metrics import GrowthMetrics


def test_first_real_dollar_requires_verified_revenue():
    metrics = GrowthMetrics(attributable_visits=100, clicks=10)
    assert metrics.first_real_dollar is False
    metrics.record_verified_order(1.0)
    assert metrics.first_real_dollar is True


def test_rates_are_observation_based():
    metrics = GrowthMetrics(attributable_visits=100, clicks=20, verified_orders=2, verified_revenue_usd=8.0, cost_usd=4.0)
    assert metrics.click_through_rate == 0.2
    assert metrics.conversion_rate == 0.1
    assert metrics.roas == 2.0


def test_source_visits_and_negative_rejection():
    metrics = GrowthMetrics()
    metrics.record_visit("owned_site", 3)
    assert metrics.attributable_visits == 3
    assert metrics.by_source["owned_site"] == 3

    try:
        metrics.record_visit("owned_site", -1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative visit count should fail")
