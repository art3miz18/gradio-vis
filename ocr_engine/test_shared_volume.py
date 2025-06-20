import os
import sys
import json
import time
import redis
import shutil
import argparse
from PIL import Image

# Check if running in Docker container
def is_running_in_docker():
    try:
        with open('/proc/1/cgroup', 'r') as f:
            return any('docker' in line for line in f)
    except:
        return False

# Set paths based on environment
if is_running_in_docker():
    SHARED_VOLUME_PATH = '/app/shared_data'
    REDIS_HOST = 'redis'
else:
    SHARED_VOLUME_PATH = os.getenv('SHARED_VOLUME_PATH', './shared_data')
    REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')

REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))

def test_redis_connection():
    """Test the Redis connection"""
    try:
        client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
        if client.ping():
            print("✅ Redis connection successful")
            return True
        else:
            print("❌ Redis ping failed")
            return False
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        return False

def test_shared_volume():
    """Test the shared volume"""
    try:
        os.makedirs(SHARED_VOLUME_PATH, exist_ok=True)
        test_file_path = os.path.join(SHARED_VOLUME_PATH, 'test_file.txt')
        
        # Write to the test file
        with open(test_file_path, 'w') as f:
            f.write(f"Test file created at {time.time()}")
        
        # Read from the test file
        with open(test_file_path, 'r') as f:
            content = f.read()
        
        # Clean up
        os.remove(test_file_path)
        
        print(f"✅ Shared volume test successful")
        print(f"   File path: {test_file_path}")
        print(f"   Content: {content}")
        return True
    except Exception as e:
        print(f"❌ Shared volume test failed: {e}")
        return False

def copy_test_image(source_path, publication_info):
    """Copy a test image to the shared volume"""
    try:
        # Create destination directory
        publication_name = publication_info.get('publicationName', 'test').replace(' ', '_').lower()
        dest_dir = os.path.join(SHARED_VOLUME_PATH, publication_name, 'test')
        os.makedirs(dest_dir, exist_ok=True)
        
        # Create destination path
        dest_path = os.path.join(dest_dir, os.path.basename(source_path))
        
        # Copy the file
        shutil.copy2(source_path, dest_path)
        
        # Create metadata
        metadata_path = os.path.join(dest_dir, 'metadata.json')
        metadata = {
            'unique_id': 'test',
            'original_path': source_path,
            'shared_path': dest_path,
            'publication_info': publication_info,
            'timestamp': time.time(),
            'file_type': os.path.splitext(source_path)[1].lstrip('.').lower()
        }
        
        # Save metadata
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        print(f"✅ Test image copied to {dest_path}")
        print(f"   Metadata saved to {metadata_path}")
        return dest_path, metadata
    except Exception as e:
        print(f"❌ Test image copy failed: {e}")
        return None, None

def send_test_message(metadata):
    """Send a test message to Redis"""
    try:
        client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
        
        # Create payload
        payload = {
            'job_id': f"test_{time.time()}",
            'shared_path': metadata.get('shared_path', ''),
            'publication_info': metadata.get('publication_info', {}),
            'file_type': metadata.get('file_type', ''),
            'timestamp': time.time()
        }
        
        # Publish to Redis
        client.publish('ocr_jobs', json.dumps(payload))
        
        # Also store in a Redis list for durability
        client.lpush('ocr_job_queue', json.dumps(payload))
        
        print(f"✅ Test message sent to Redis")
        print(f"   Payload: {json.dumps(payload, indent=2)}")
        return True
    except Exception as e:
        print(f"❌ Test message failed: {e}")
        return False

def create_test_image(width, height):
    """Create a test image with text"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        
        # Create a new image
        img = Image.new('RGB', (width, height), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        
        # Add some text
        text = f"Test Image {width}x{height}"
        draw.text((width//4, height//2), text, fill=(0, 0, 0))
        
        # Save the image
        path = os.path.join('/tmp', f"test_image_{width}x{height}.jpg")
        img.save(path)
        
        print(f"✅ Created test image: {path}")
        return path
    except Exception as e:
        print(f"❌ Failed to create test image: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description='Test shared volume and Redis messaging')
    parser.add_argument('--image', type=str, help='Path to test image')
    parser.add_argument('--publication', type=str, default='Test Publication', help='Publication name')
    parser.add_argument('--edition', type=str, default='Test Edition', help='Edition name')
    parser.add_argument('--language', type=str, default='English', help='Language')
    parser.add_argument('--zone', type=str, default='Test Zone', help='Zone name')
    args = parser.parse_args()
    
    print(f"Testing environment:")
    print(f"  Running in Docker: {is_running_in_docker()}")
    print(f"  Shared volume path: {SHARED_VOLUME_PATH}")
    print(f"  Redis host: {REDIS_HOST}:{REDIS_PORT}")
    print()
    
    # Test Redis connection
    redis_ok = test_redis_connection()
    
    # Test shared volume
    volume_ok = test_shared_volume()
    
    if not redis_ok or not volume_ok:
        print("❌ Basic tests failed. Please fix the issues and try again.")
        return
    
    # Get test image
    image_path = args.image
    if not image_path:
        # Create a test image
        image_path = create_test_image(800, 600)
        if not image_path:
            print("❌ Failed to create test image. Please provide an image path.")
            return
    
    # Check if the image exists
    if not os.path.exists(image_path):
        print(f"❌ Image not found: {image_path}")
        return
    
    # Create publication info
    publication_info = {
        'publicationName': args.publication,
        'editionName': args.edition,
        'languageName': args.language,
        'zoneName': args.zone
    }
    
    # Copy test image
    dest_path, metadata = copy_test_image(image_path, publication_info)
    if not dest_path:
        return
    
    # Send test message
    send_test_message(metadata)

if __name__ == '__main__':
    main()