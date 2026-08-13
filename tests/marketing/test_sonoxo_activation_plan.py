from superagi.marketing.sonoxo_activation_plan import ChannelAuthorization, SonoxoActivationPlanner


def test_million_workers_are_logical_and_bounded():
    planner = SonoxoActivationPlanner()
    manifest = planner.launch_manifest(requested_per_order=1_000_000)
    assert manifest["worker_capacity"] == 1_000_000
    assert manifest["max_active_per_order"] == 100
    assert all(v <= 100 for v in manifest["action_counts"].values())


def test_external_channels_blocked_without_authorization():
    planner = SonoxoActivationPlanner()
    plan = planner.channel_plan()
    external = [item for item in plan if item.channel in {"authorized_social", "opt_in_email"}]
    assert external
    assert all(item.status == "blocked" for item in external)


def test_authorized_social_requires_explicit_reference():
    planner = SonoxoActivationPlanner()
    no_reference = planner.channel_plan([ChannelAuthorization("authorized_social", True, "")])
    assert all(item.status == "blocked" for item in no_reference if item.channel == "authorized_social")

    connected = planner.channel_plan([ChannelAuthorization("authorized_social", True, "oauth-connection-1")])
    assert all(item.status == "ready" for item in connected if item.channel == "authorized_social")


def test_owned_site_merch_asset_is_ready_and_attributed():
    planner = SonoxoActivationPlanner()
    owned = [item for item in planner.channel_plan() if item.channel == "owned_site"]
    assert owned
    assert all(item.status == "ready" for item in owned)
    assert any("utm_campaign=sonoxo-first-real-dollar" in item.tracked_url for item in owned)


def test_manifest_preserves_compliance_rules():
    manifest = SonoxoActivationPlanner().launch_manifest(requested_per_order=2)
    assert manifest["rules"]["fake_engagement"] == "forbidden"
    assert manifest["rules"]["soundcloud_activity"] == "genuine_user_driven_only"
    assert manifest["rules"]["commerce_revenue"] == "authorized_source_verification_required"
