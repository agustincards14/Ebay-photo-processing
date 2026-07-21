---
name: check-api-connection
description: "Use when: checking if the GEMINI_API_KEY is configured correctly and performing a lightweight ping to the Google GenAI API to ensure connectivity before starting a batch job."
---

# Check API Connection Skill

This skill ensures that the Google GenAI `GEMINI_API_KEY` is properly configured, and validates the connection with a quick API ping before proceeding with large processing jobs.

## Workflow

1.  **Check Environment Variable Setup**:
    Verify that the `GEMINI_API_KEY` environment variable is available. If it is missing, instruct the user on how to set it up before proceeding.

2.  **Create Lightweight Ping Script**:
    Create a temporary Python script (e.g., `_ping_gemini.py`) with the following logic:
    - Import the Google GenAI client (`from google import genai`).
    - Attempt to initialize the client (`client = genai.Client()`).
    - Perform a lightweight API call (e.g., retrieving information about the `gemini-2.5-pro` model or creating a tiny generation).
    - Print a success message if the connection is established.

    *Example snippet to embed:*
    ```python
    import os
    import sys
    from google import genai
    
    def test_connection():
        if not os.environ.get("GEMINI_API_KEY"):
            print("ERROR: GEMINI_API_KEY is not set.")
            sys.exit(1)
        try:
            client = genai.Client()
            # Lightweight call to check connectivity
            model_info = client.models.get(model='gemini-2.5-pro')
            print(f"SUCCESS: Connected! Model {model_info.name} is available.")
        except Exception as e:
            print(f"ERROR: Failed to connect to Gemini API. Details: {e}")
            sys.exit(1)
            
    if __name__ == "__main__":
        test_connection()
    ```

3.  **Execute the Script**:
    Run the script using the terminal `python _ping_gemini.py`. 
    Observe the output and check if the connection was successful.

4.  **Cleanup**:
    Remove the temporary `_ping_gemini.py` script after the test.

5.  **Report the findings**:
    Inform the user whether the API connection test succeeded or failed. If it failed, offer troubleshooting advice on correcting the API key.
