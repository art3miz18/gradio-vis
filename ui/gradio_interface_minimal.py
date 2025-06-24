import os
import requests
import gradio as gr
import time
import threading

GATEWAY_BASE_URL = os.environ.get("GATEWAY_BASE_URL", "http://gateway:5001")

# Global state for tracking processing
processing_state = {
    "current_task_id": None,
    "status": "idle",
    "progress": 0,
    "step": "Waiting",
    "start_time": None,
    "articles": []
}

def display_pdf_preview(pdf_file):
    """Display PDF preview and extract basic info"""
    if pdf_file is None:
        return "No PDF selected"
    
    try:
        file_size = os.path.getsize(pdf_file.name) if pdf_file.name else 0
        file_info = f"""📄 **PDF Information**
- **File**: {os.path.basename(pdf_file.name) if pdf_file.name else 'Unknown'}
- **Size**: {file_size / (1024*1024):.2f} MB
- **Status**: Ready for processing"""
        return file_info
    except Exception as e:
        return f"Error reading PDF: {str(e)}"

def submit_pdf_with_tracking(pdf_file, publication, edition, language, zone, date, dpi, quality, resize_bool, base_url):
    """Enhanced PDF submission with progress tracking"""
    if pdf_file is None:
        return "❌ No PDF file selected", 0, "No processing active", ""
    
    global processing_state
    processing_state = {
        "current_task_id": None,
        "status": "submitting",
        "progress": 0,
        "step": "Submitting PDF...",
        "start_time": time.time(),
        "articles": []
    }
    
    try:
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
        response_data = resp.json()
        
        if resp.status_code == 200 and "task_id" in response_data:
            processing_state["current_task_id"] = response_data["task_id"]
            processing_state["status"] = "processing"
            processing_state["step"] = "PDF submitted, processing started..."
            processing_state["progress"] = 10
            
            # Start polling in background
            threading.Thread(target=poll_task_status, args=(response_data["task_id"], base_url), daemon=True).start()
            
            return (f"✅ Task submitted successfully\n**Task ID**: {response_data['task_id']}\n**Status**: Processing started", 
                   10, "PDF submitted, processing started...", "")
        else:
            processing_state["status"] = "error"
            return f"❌ Error {resp.status_code}: {resp.text}", 0, "Error occurred", ""
            
    except Exception as e:
        processing_state["status"] = "error"
        return f"❌ Exception: {str(e)}", 0, "Error occurred", ""

def poll_task_status(task_id, base_url):
    """Poll task status and update global state"""
    global processing_state
    
    while processing_state["status"] == "processing":
        try:
            # Try enhanced progress endpoint first
            resp = requests.get(f"{base_url.rstrip('/')}/tasks/{task_id}/progress")
            if resp.status_code == 200:
                progress_data = resp.json()
                
                processing_state["progress"] = progress_data.get("overall_progress", 0)
                processing_state["step"] = progress_data.get("message", "Processing...")
                processing_state["articles"] = progress_data.get("articles", [])
                
                current_step = progress_data.get("current_step", "")
                celery_state = progress_data.get("celery_state", "UNKNOWN")
                
                if current_step == "completed" or celery_state == "SUCCESS":
                    processing_state["status"] = "completed"
                    processing_state["progress"] = 100
                    processing_state["step"] = "Processing completed successfully!"
                    break
                elif current_step == "failed" or celery_state == "FAILURE":
                    processing_state["status"] = "error"
                    processing_state["step"] = f"Processing failed: {progress_data.get('message', 'Unknown error')}"
                    break
                    
        except Exception as e:
            print(f"Error polling task status: {e}")
            
        time.sleep(3)  # Poll every 3 seconds

def get_processing_status():
    """Get current processing status for UI updates"""
    global processing_state
    
    if processing_state["status"] == "idle":
        return "⏸️ Idle - No active processing", 0, "Waiting for input", ""
    
    elapsed_time = time.time() - processing_state["start_time"] if processing_state["start_time"] else 0
    
    status_text = f"""🔄 **Processing Status**
- **Task ID**: {processing_state.get('current_task_id', 'N/A')}
- **Current Step**: {processing_state['step']}
- **Progress**: {processing_state['progress']}%
- **Elapsed Time**: {elapsed_time:.1f}s
- **Status**: {processing_state['status'].title()}"""
    
    results_display = create_results_display(processing_state.get("articles", []))
    
    return status_text, processing_state["progress"], processing_state["step"], results_display

def create_results_display(articles):
    """Create formatted analysis results display"""
    if not articles:
        return "No articles processed yet"
    
    results_html = f"<div style='max-height: 400px; overflow-y: auto;'><h3>📰 Processed Articles ({len(articles)})</h3>"
    
    for i, article in enumerate(articles, 1):
        ministry = article.get('ministryName', 'Unknown')
        sentiment = article.get('sentiment', 'Neutral')
        heading = article.get('heading', 'No heading')
        summary = article.get('english_summary', 'No summary available')
        
        # Color coding based on sentiment
        sentiment_color = {
            'Positive': '#10b981',
            'Negative': '#ef4444', 
            'Neutral': '#6b7280'
        }.get(sentiment, '#6b7280')
        
        results_html += f"""
        <div style='border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; margin-bottom: 12px; background: #f9fafb;'>
            <div style='display: flex; justify-content: space-between; align-items: start; margin-bottom: 8px;'>
                <h4 style='margin: 0; color: #1f2937; font-size: 14px;'>📄 Article {i}</h4>
                <div style='display: flex; gap: 6px;'>
                    <span style='background: #dbeafe; color: #1e40af; padding: 2px 6px; border-radius: 10px; font-size: 11px;'>
                        🏛️ {ministry}
                    </span>
                    <span style='background: {sentiment_color}20; color: {sentiment_color}; padding: 2px 6px; border-radius: 10px; font-size: 11px;'>
                        😊 {sentiment}
                    </span>
                </div>
            </div>
            <div style='margin-bottom: 6px;'>
                <strong style='color: #374151;'>Heading:</strong> {heading}
            </div>
            <div>
                <strong style='color: #374151;'>Summary:</strong> {summary}
            </div>
        </div>
        """
    
    results_html += "</div>"
    return results_html

def main():
    demo = gr.Blocks(title="OCR Pipeline Dashboard")
    
    with demo:
        gr.Markdown("# 🏭 Production OCR Pipeline Dashboard")
        
        with gr.Tab("📄 PDF Processing"):
            with gr.Row():
                # Left column - Input form
                with gr.Column(scale=1):
                    gr.Markdown("### 📤 Upload & Configuration")
                    
                    base_url_box = gr.Textbox(value=GATEWAY_BASE_URL, label="Gateway URL")
                    pdf_file = gr.File(label="PDF File", file_types=[".pdf"])
                    pdf_info = gr.Markdown("Select a PDF file to begin")
                    
                    with gr.Row():
                        publication = gr.Textbox(label="Publication", placeholder="e.g., Times of India")
                        edition = gr.Textbox(label="Edition", value="", placeholder="e.g., Delhi")
                    
                    with gr.Row():
                        language = gr.Textbox(label="Language", placeholder="e.g., English")
                        zone = gr.Textbox(label="Zone", placeholder="e.g., North")
                        
                    date = gr.Textbox(label="Date (dd-mm-yyyy)", placeholder="22-06-2025")
                    
                    with gr.Row():
                        dpi = gr.Number(value=200, label="DPI")
                        quality = gr.Number(value=85, label="Quality")
                        resize = gr.Checkbox(value=True, label="Auto Resize")
                    
                    pdf_btn = gr.Button("🚀 Start Processing", variant="primary")
                
                # Right column - Status and results
                with gr.Column(scale=2):
                    gr.Markdown("### 📊 Processing Status & Results")
                    
                    status_display = gr.Markdown("⏸️ Ready to process")
                    progress_bar = gr.Slider(minimum=0, maximum=100, value=0, label="Progress", interactive=False)
                    step_info = gr.Textbox(label="Current Step", value="Waiting for input", interactive=False)
                    
                    results_display = gr.HTML("No results yet")
                    
                    refresh_btn = gr.Button("🔄 Refresh Status")
            
            # Event handlers
            pdf_file.change(
                display_pdf_preview,
                inputs=[pdf_file],
                outputs=[pdf_info]
            )
            
            pdf_btn.click(
                submit_pdf_with_tracking,
                inputs=[pdf_file, publication, edition, language, zone, date, dpi, quality, resize, base_url_box],
                outputs=[status_display, progress_bar, step_info, results_display]
            )
            
            refresh_btn.click(
                get_processing_status,
                outputs=[status_display, progress_bar, step_info, results_display]
            )
        
        gr.Markdown("""
        ---
        💡 **Tips**: 
        - Use high DPI (300+) for better OCR accuracy
        - Click 'Refresh Status' to see processing updates
        - Processing time varies with PDF size and complexity
        """)
    
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False)

if __name__ == "__main__":
    main()