import asyncio
# We don't import genai or models here directly anymore, they are managed by config.py and used by calling functions
# This file might just contain the async calling wrapper IF the model instance is passed to it,
# OR functions in content_analyzer.py will directly use the models from config.py.

# For main content analysis (the big prompt)
async def call_main_content_analysis_gemini(image_data: dict):
    """
    Asynchronously calls the pre-configured main content analysis Gemini model.
    """
    from config import content_analyzer_model_instance # Get the process-specific model

    if not content_analyzer_model_instance:
        print(f"Process {os.getpid()}: Main content analysis model not available.")
        return None
    try:
        response = await asyncio.to_thread(
            content_analyzer_model_instance.generate_content,
            contents=[image_data] # The system prompt is part of the model_instance
        )
        if response and hasattr(response, 'text') and response.text:
             return response.text
        # ... (handle empty response, blocked prompt etc.) ...
        print(f"Process {os.getpid()}: Gemini API (main analysis) returned empty or problematic response.")
        return None
    except Exception as e:
        print(f"Process {os.getpid()}: Gemini API Error (main analysis): {e}")
        import traceback
        traceback.print_exc()
        return None