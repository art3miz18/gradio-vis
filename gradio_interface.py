import os
import json
import requests
import gradio as gr

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")


def submit_pdf(pdf_file, publication, edition, language, zone, date, dpi, quality, resize_bool, base_url):
    url = base_url.rstrip('/') + '/pipeline'
    files = {"pdf": (os.path.basename(pdf_file.name), pdf_file, "application/pdf")}
    data = {
        "publicationName": publication,
        "editionName": edition,
        "languageName": language,
        "zoneName": zone,
        "date": date,
        "dpi": int(dpi),
        "quality": int(quality),
        "resize_bool": resize_bool,
    }
    resp = requests.post(url, data=data, files=files)
    try:
        return json.dumps(resp.json(), indent=2)
    except Exception:
        return f"Error {resp.status_code}: {resp.text}"


def submit_direct_images(image_dir, publication, edition, language, zone, date, base_url):
    url = base_url.rstrip('/') + '/process/direct_images'
    payload = {
        "imageDirectory": image_dir,
        "publicationName": publication,
        "editionName": edition,
        "languageName": language,
        "zoneName": zone,
        "date": date,
    }
    resp = requests.post(url, json=payload)
    try:
        return json.dumps(resp.json(), indent=2)
    except Exception:
        return f"Error {resp.status_code}: {resp.text}"


def submit_raw_json(title, content, base_url):
    url = base_url.rstrip('/') + '/process/digital_raw_json'
    payload = {
        "title": title,
        "content": content,
    }
    resp = requests.post(url, json=payload)
    try:
        return json.dumps(resp.json(), indent=2)
    except Exception:
        return f"Error {resp.status_code}: {resp.text}"


def build_interface():
    base_url_box = gr.Textbox(value=GATEWAY_URL, label="Gateway URL")

    with gr.Tab("Upload PDF"):
        pdf_file = gr.File(label="PDF File")
        publication = gr.Textbox(label="Publication Name")
        edition = gr.Textbox(label="Edition Name", value="")
        language = gr.Textbox(label="Language Name")
        zone = gr.Textbox(label="Zone Name")
        date = gr.Textbox(label="Date (dd-mm-yyyy)")
        dpi = gr.Number(value=200, label="DPI")
        quality = gr.Number(value=85, label="JPEG Quality")
        resize = gr.Checkbox(value=True, label="Resize Images")
        pdf_output = gr.Textbox(label="Response")
        pdf_btn = gr.Button("Submit PDF")
        pdf_btn.click(
            submit_pdf,
            [pdf_file, publication, edition, language, zone, date, dpi, quality, resize, base_url_box],
            pdf_output,
        )

    with gr.Tab("Direct Images"):
        img_dir = gr.Textbox(label="Image Directory Path")
        publication_i = gr.Textbox(label="Publication Name")
        edition_i = gr.Textbox(label="Edition Name", value="")
        language_i = gr.Textbox(label="Language Name")
        zone_i = gr.Textbox(label="Zone Name")
        date_i = gr.Textbox(label="Date (dd-mm-yyyy)")
        img_output = gr.Textbox(label="Response")
        img_btn = gr.Button("Submit Images")
        img_btn.click(
            submit_direct_images,
            [img_dir, publication_i, edition_i, language_i, zone_i, date_i, base_url_box],
            img_output,
        )

    with gr.Tab("Raw JSON"):
        title_j = gr.Textbox(label="Title")
        content_j = gr.Textbox(label="Content", lines=10)
        json_output = gr.Textbox(label="Response")
        json_btn = gr.Button("Submit JSON")
        json_btn.click(
            submit_raw_json,
            [title_j, content_j, base_url_box],
            json_output,
        )


def main():
    with gr.Blocks(title="Gateway Client") as demo:
        gr.Markdown("# Gradio Gateway Client")
        build_interface()
    demo.launch()


if __name__ == "__main__":
    main()
