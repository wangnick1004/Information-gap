import os
from dotenv import load_dotenv

# Load API key from .env file
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("❌ Error: GEMINI_API_KEY is not set in your .env file or environment.")
    exit(1)

print(f"🔑 Using API key: {api_key[:6]}...{api_key[-4:] if len(api_key) > 10 else ''}")
print("🔍 Fetching available Gemini models for your API key...\n")

# Try google.genai (New Google GenAI SDK)
try:
    from google import genai

    client = genai.Client(api_key=api_key)
    print("=== Available Models via google.genai ===")
    models = list(client.models.list())
    if not models:
        print("No models returned.")
    for model in models:
        # Check supported actions/methods if available
        name = model.name
        supported_methods = getattr(model, "supported_generation_methods", getattr(model, "supported_actions", None))
        display_name = getattr(model, "display_name", "")
        print(f"- {name} ({display_name})" if display_name else f"- {name}")
        if supported_methods:
            print(f"  Actions: {supported_methods}")

except ImportError:
    # Fallback to legacy google.generativeai if installed
    try:
        import google.generativeai as legacy_genai

        legacy_genai.configure(api_key=api_key)
        print("=== Available Models via google.generativeai ===")
        for model in legacy_genai.list_models():
            if "generateContent" in model.supported_generation_methods:
                print(f"- {model.name} (Methods: {model.supported_generation_methods})")
    except Exception as exc:
        print(f"❌ Error checking models: {exc}")
except Exception as exc:
    print(f"❌ Error querying models: {exc}")
