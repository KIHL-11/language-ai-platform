"""Manual one-request AI smoke test; excluded from automated test execution."""

from services.ai.llm_client import llm


def main():
    result = llm.chat(
        [
            {
                "role": "user",
                "content": "Explain the sentence: The weather is getting colder.",
            }
        ]
    )
    print(result)


if __name__ == "__main__":
    main()
