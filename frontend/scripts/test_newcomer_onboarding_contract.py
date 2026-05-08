from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_contains(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing {label}: {needle}")


def main() -> None:
    onboarding = read("src/app/profile/components/ProfileOnboarding.tsx")
    profile_page = read("src/app/profile/page.tsx")

    assert_contains(onboarding, "buildOnboardingArchive", "archive builder")
    assert_contains(onboarding, "可投递档案", "deliverable archive copy")
    assert_contains(onboarding, "生成可投递档案", "finish action")
    assert_contains(onboarding, "还差", "missing item guidance")
    assert_contains(onboarding, "updateProfileData", "profile persistence")
    assert_contains(profile_page, "ProfileOnboarding", "profile page onboarding integration")
    assert_contains(profile_page, "showOnboarding", "profile page onboarding state")

    print("newcomer onboarding contract ok")


if __name__ == "__main__":
    main()
