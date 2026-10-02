"""Quote state-path assignments for a Desktop Entry Exec field, not a shell."""

import sys


def quote_argument(value: str) -> str:
    # Desktop entries decode backslashes before parsing Exec arguments, so a
    # literal backslash needs four. Percent signs must not become field codes.
    value = value.replace("\\", "\\\\\\\\")
    for character in ('"', "`", "$"):
        value = value.replace(character, "\\\\" + character)
    value = value.replace("%", "%%")
    return '"' + value + '"'


def environment_arguments(
    data_directory: str, cache_directory: str, state_directory: str
) -> str:
    assignments = (
        ("FUSION360_DATA_HOME", data_directory),
        ("FUSION360_CACHE_HOME", cache_directory),
        ("FUSION360_STATE_HOME", state_directory),
    )
    return " ".join(quote_argument(key + "=" + value) for key, value in assignments)


if __name__ == "__main__":
    data_directory, cache_directory, state_directory = sys.argv[1:]
    print(environment_arguments(data_directory, cache_directory, state_directory))
