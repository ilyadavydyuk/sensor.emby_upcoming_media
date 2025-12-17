"""Client."""
import datetime
import logging
import os
import hashlib
from pathlib import Path
import threading
import time

_LOGGER = logging.getLogger(__name__)


class EmbyClient:
    """Client class"""

    def __init__(self, host, api_key, ssl, port, max_items, user_id, show_episodes, img_dir, img_cache_days=30):
        """Init."""
        self.data = {}
        self.host = host
        self.ssl = "s" if ssl else ""
        self.port = port
        self.api_key = api_key
        self.user_id = user_id
        self.max_items = max_items
        self.show_episodes = "&GroupItems=False" if show_episodes else ""
        self.img_dir = img_dir
        self.img_cache_days = img_cache_days
        
        # Create image directory if it doesn't exist
        if self.img_dir:
            os.makedirs(self.img_dir, exist_ok=True)
            _LOGGER.info("Image directory: %s", self.img_dir)
            
            # Clean old images on initialization
            self.cleanup_old_images()

    def get_view_categories(self):
        """This will pull the list of all View Categories on Emby"""
        import requests
        
        try:
            url = "http{0}://{1}:{2}/Users/{3}/Views?api_key={4}".format(
                self.ssl, self.host, self.port, self.user_id, self.api_key
            )
            api = requests.get(url, timeout=10)
        except OSError:
            _LOGGER.warning("Host %s is not available", self.host)
            self._state = "%s cannot be reached" % self.host
            return

        if api.status_code == 200:
            self.data["ViewCategories"] = api.json()["Items"]
        else:
            _LOGGER.warning("Could not reach url %s", url)
            self._state = "%s cannot be reached" % self.host

        return self.data["ViewCategories"]

    def get_data(self, categoryId):
        import requests
        
        try:
            url = "http{0}://{1}:{2}/Users/{3}/Items/Latest?Limit={4}&Fields=CommunityRating,Studios,PremiereDate,Genres,ChildCount,ProductionYear,DateCreated&ParentId={5}&api_key={6}{7}".format(
                self.ssl,
                self.host,
                self.port,
                self.user_id,
                self.max_items,
                categoryId,
                self.api_key,
                self.show_episodes,
            )
            api = requests.get(url, timeout=10)
        except OSError:
            _LOGGER.warning("Host %s is not available", self.host)
            self._state = "%s cannot be reached" % self.host
            return

        if api.status_code == 200:
            self._state = "Online"
            self.data[categoryId] = api.json()[: self.max_items]
        else:
            _LOGGER.warning("Could not reach url %s", url)
            self._state = "%s cannot be reached" % self.host
            return

        return self.data[categoryId]

    def download_image_sync(self, url, filename):
        """Download image from Emby server and save locally"""
        import requests
        
        try:
            response = requests.get(url, timeout=10, stream=True)
            
            if response.status_code == 200:
                filepath = os.path.join(self.img_dir, filename)
                
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(1024):
                        f.write(chunk)
                
                if os.path.exists(filepath):
                    file_size = os.path.getsize(filepath)
                    _LOGGER.info("Downloaded: %s (%s bytes)", filename, file_size)
                    return True
                else:
                    _LOGGER.error("File not created: %s", filepath)
                    return False
            else:
                _LOGGER.warning("Download failed, status: %s", response.status_code)
                return False
        except Exception as e:
            _LOGGER.error("Error downloading image: %s", str(e))
            return False

    def cleanup_old_images(self):
        """Remove images older than img_cache_days"""
        if not self.img_dir or not os.path.exists(self.img_dir):
            return
        
        try:
            current_time = time.time()
            cutoff_time = current_time - (self.img_cache_days * 86400)  # days to seconds
            deleted_count = 0
            
            for filename in os.listdir(self.img_dir):
                filepath = os.path.join(self.img_dir, filename)
                
                # Only process .jpg files
                if not filename.endswith('.jpg'):
                    continue
                
                # Check file modification time
                try:
                    file_mtime = os.path.getmtime(filepath)
                    if file_mtime < cutoff_time:
                        os.remove(filepath)
                        deleted_count += 1
                        _LOGGER.debug("Deleted old image: %s", filename)
                except Exception as e:
                    _LOGGER.warning("Could not delete %s: %s", filename, str(e))
            
            if deleted_count > 0:
                _LOGGER.info("Cleaned up %d old images (older than %d days)", deleted_count, self.img_cache_days)
        
        except Exception as e:
            _LOGGER.error("Error during cleanup: %s", str(e))

    def get_image_url(self, itemId, imageType):
        """Get image URL - returns local path and starts download in background if needed"""
        # Build the original Emby URL with API key
        emby_url = "http{0}://{1}:{2}/Items/{3}/Images/{4}?maxHeight=360&maxWidth=640&quality=90&api_key={5}".format(
            self.ssl, self.host, self.port, itemId, imageType, self.api_key
        )
        
        # If img_dir is not set, return direct Emby URL (old behavior)
        if not self.img_dir:
            return emby_url
        
        # Generate filename
        filename_base = f"{itemId}_{imageType}"
        filename_hash = hashlib.md5(filename_base.encode()).hexdigest()[:8]
        filename = f"{filename_base}_{filename_hash}.jpg"
        filepath = os.path.join(self.img_dir, filename)
        
        # Check if image already exists
        if not os.path.exists(filepath):
            # Start download in background thread
            download_thread = threading.Thread(
                target=self.download_image_sync,
                args=(emby_url, filename),
                daemon=True
            )
            download_thread.start()
        
        # Return local URL path (even if download is still in progress)
        if '/www/' in self.img_dir:
            local_path = self.img_dir.split('/www/')[-1]
            return f"/local/{local_path}/{filename}"
        else:
            _LOGGER.warning("Image directory doesn't contain /www/")
            return emby_url