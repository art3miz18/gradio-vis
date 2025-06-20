import os
import time
import requests
import gradio as gr

BASE_URL = os.getenv("GATEWAY_BASE_URL", "http://localhost:5001")


def poll_task(task_id: str, interval: float = 2.0, timeout: float = 60.0):
    """Poll the /tasks/{task_id} endpoint until completion or timeout."""
    url = f"{BASE_URL}/tasks/{task_id}"
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                state = data.get("state")
                if state in {"SUCCESS", "FAILURE"}:
                    return data
            else:
                return {"error": f"status {resp.status_code}", "detail": resp.text}
        except Exception as e:
            return {"error": str(e)}
        time.sleep(interval)
    return {"error": "Timed out waiting for task"}


def upload_pdf(pdf_file, publication, edition, language, zone, date, dpi, quality, resize):
    if pdf_file is None:
        return {"error": "No PDF provided"}
    files = {"pdf": (pdf_file.name, pdf_file, "application/pdf")}
    data = {
        "publicationName": publication,
        "editionName": edition,
        "languageName": language,
        "zoneName": zone,
        "date": date,
        "dpi": dpi,
        "quality": quality,
        "resize_bool": str(bool(resize)).lower(),
    }
    try:
        resp = requests.post(f"{BASE_URL}/pipeline", files=files, data=data, timeout=30)
        resp.raise_for_status()
        task_id = resp.json().get("task_id")
        if task_id:
            return poll_task(task_id)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def upload_image(image_file, publication, edition, language, zone, date, dpi, quality, resize):
    if image_file is None:
        return {"error": "No image provided"}
    files = {"image": (image_file.name, image_file, image_file.mimetype or "image/jpeg")}
    data = {
        "publicationName": publication,
        "editionName": edition,
        "languageName": language,
        "zoneName": zone,
        "date": date,
        "dpi": dpi,
        "quality": quality,
        "resize_bool": str(bool(resize)).lower(),
    }
    try:
        resp = requests.post(f"{BASE_URL}/process/single_image", files=files, data=data, timeout=30)
        resp.raise_for_status()
        task_id = resp.json().get("task_id")
        if task_id:
            return poll_task(task_id)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def runtime_prompt(prompt_text):
    if not prompt_text:
        return {"error": "Prompt text is empty"}
    try:
        resp = requests.post(f"{BASE_URL}/runtime_prompt", json={"prompt": prompt_text}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        task_id = data.get("task_id")
        if task_id:
            return poll_task(task_id)
        return data
    except Exception as e:
        return {"error": str(e)}


with gr.Blocks() as demo:
    gr.Markdown("## Newspaper OCR & Analysis Interface")
    with gr.Tab("Upload PDF"):
        pdf_input = gr.File(label="PDF File", file_types=[".pdf"])
        publication = gr.Textbox(label="Publication Name")
        edition = gr.Textbox(label="Edition Name", value="")
        language = gr.Textbox(label="Language", value="English")
        zone = gr.Textbox(label="Zone", value="")
        date = gr.Textbox(label="Date (DD-MM-YYYY)")
        dpi = gr.Number(label="DPI", value=200)
        quality = gr.Number(label="Quality", value=85)
        resize = gr.Checkbox(label="Resize Images", value=True)
        pdf_output = gr.JSON(label="Result")
        pdf_btn = gr.Button("Submit")
        pdf_btn.click(upload_pdf, [pdf_input, publication, edition, language, zone, date, dpi, quality, resize], pdf_output)

    with gr.Tab("Process Image"):
        img_input = gr.File(label="Image File", file_types=["image"])
        pub2 = gr.Textbox(label="Publication Name")
        edi2 = gr.Textbox(label="Edition Name", value="")
        lang2 = gr.Textbox(label="Language", value="English")
        zone2 = gr.Textbox(label="Zone", value="")
        date2 = gr.Textbox(label="Date (DD-MM-YYYY)")
        dpi2 = gr.Number(label="DPI", value=200)
        quality2 = gr.Number(label="Quality", value=85)
        resize2 = gr.Checkbox(label="Resize Images", value=True)
        img_output = gr.JSON(label="Result")
        img_btn = gr.Button("Submit")
        img_btn.click(upload_image, [img_input, pub2, edi2, lang2, zone2, date2, dpi2, quality2, resize2], img_output)

    with gr.Tab("Custom Prompt"):
        prompt_area = gr.Textbox(label="Prompt", lines=6)
        prompt_output = gr.JSON(label="Result")
        prompt_btn = gr.Button("Analyze")
        prompt_btn.click(runtime_prompt, prompt_area, prompt_output)

    gr.Markdown("Base URL: " + BASE_URL)

if __name__ == "__main__":
    demo.launch()
