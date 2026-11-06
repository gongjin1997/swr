from datetime import datetime, timezone

from swr_builder.policy import deployment_policy_block


def test_freeze_is_enforced_until_its_timezone_aware_expiry():
    policy = {
        'freeze': {'enabled': True, 'until': '2026-11-06T12:00:00+00:00'},
        'deploy_windows': [],
    }

    assert deployment_policy_block(policy, datetime(2026, 11, 6, 11, 59, tzinfo=timezone.utc))
    assert deployment_policy_block(policy, datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)) is None


def test_deployment_windows_use_iso_weekdays_and_allow_cross_midnight():
    policy = {
        'freeze': {'enabled': False},
        'deploy_windows': [{
            'days': [5],
            'start': '22:00',
            'end': '02:00',
            'tz': 'Asia/Tokyo',
        }],
    }

    assert deployment_policy_block(policy, datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc)) is None
    assert deployment_policy_block(policy, datetime(2026, 11, 6, 16, 30, tzinfo=timezone.utc)) is None
    assert deployment_policy_block(policy, datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc))


def test_any_matching_window_allows_release_and_invalid_window_fails_closed():
    now = datetime(2026, 11, 6, 10, 30, tzinfo=timezone.utc)
    policy = {
        'freeze': {'enabled': False},
        'deploy_windows': [
            {'days': [4], 'start': '09:00', 'end': '10:00', 'tz': 'UTC'},
            {'days': [5], 'start': '10:00', 'end': '11:00', 'tz': 'UTC'},
        ],
    }
    assert deployment_policy_block(policy, now) is None

    policy['deploy_windows'][1]['tz'] = 'Invalid/Zone'
    assert deployment_policy_block(policy, now) == 'namespace release window is invalid'
