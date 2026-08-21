"""Marketing channels collector for social media and promotional platform data."""

from __future__ import annotations

import logging
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .base import BaseCollector
from ..models import CompanyMatch, DataSource, RawCompanyData

logger = logging.getLogger(__name__)

# 百度搜索URL
BAIDU_SEARCH_URL = "https://www.baidu.com/s"


class MarketingChannelsAdapter(BaseCollector):
    """Collector that searches for marketing channels (social media accounts)
    for a company using百度 search.
    
    Supports: WeChat Official Accounts, Weibo, Douyin, Xiaohongshu, Video Channels.
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": "Mozilla/5.0 (compatible; LookalikeBot/0.1)"}
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def search(self, company_name: str) -> list[CompanyMatch]:
        """Search for company's marketing channels using 百度 search."""
        channels = self._search_channels(company_name)
        
        if not channels:
            return []
        
        # Return a match with the company name and collected channel info
        return [
            CompanyMatch(
                company_id=company_name,
                company_name=company_name,
                legal_representative="",
                source="marketing_channels",
                confidence=0.7,
            )
        ]

    def collect(self, company_id: str) -> RawCompanyData:
        """Collect marketing channel data for a company.
        
        company_id is treated as company_name.
        """
        raw = RawCompanyData(company_id=company_id, company_name=company_id)
        
        try:
            channels = self._search_channels(company_id)
            if channels:
                now = datetime.now()
                raw.market_activity["marketing_channels"] = channels
                raw.sources.append(
                    DataSource(
                        "marketing_channels",
                        "baidu://search",
                        now,
                        "marketing_channels",
                    )
                )
        except Exception:
            logger.error("Marketing channels collector failed for %s", company_id, exc_info=True)

        return raw

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _search_channels(self, company_name: str) -> dict:
        """Search for marketing channels for a company using 百度 search.
        
        Returns a dict with channel info for: wechat, weibo, douyin, xiaohongshu, video_channels.
        """
        channels: dict = {
            "wechat": [],
            "weibo": [],
            "douyin": [],
            "xiaohongshu": [],
            "video_channels": [],
        }
        
        # Search for each channel type
        channel_configs = [
            ("wechat", "微信公众号", "gh_"),
            ("weibo", "微博", "@"),
            ("douyin", "抖音", "douyin.com"),
            ("xiaohongshu", "小红书", "xiaohongshu.com"),
        ]
        
        for channel_type, search_keyword, url_pattern in channel_configs:
            try:
                results = self._search_channel(company_name, search_keyword, url_pattern)
                channels[channel_type] = results
            except Exception:
                logger.debug("Failed to search %s for %s", channel_type, company_name, exc_info=True)
        
        # Search for video channels (including Tencent Video, iQiyi, etc.)
        try:
            video_results = self._search_video_channels(company_name)
            channels["video_channels"] = video_results
        except Exception:
            logger.debug("Failed to search video channels for %s", company_name, exc_info=True)
        
        return channels

    def _search_channel(self, company_name: str, search_keyword: str, url_pattern: str) -> list:
        """Search for a specific channel type and extract account info."""
        results = []
        
        # Construct search query
        query = f"{company_name} {search_keyword}"
        
        try:
            resp = self._session.get(
                BAIDU_SEARCH_URL, 
                params={"wd": query}, 
                timeout=15
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Parse search results
            for item in soup.select(".result, .c-container"):
                title_el = item.select_one(".t, h3")
                if not title_el:
                    continue
                    
                title = title_el.get_text(strip=True)
                link_el = item.select_one("a")
                if not link_el:
                    continue
                    
                url = link_el.get("href", "")
                
                # Check if URL matches the pattern
                if url_pattern and url_pattern not in url:
                    continue
                
                # Extract account name from title or URL
                account_name = self._extract_account_name(title, url, search_keyword)
                
                if account_name:
                    results.append({
                        "account_name": account_name,
                        "url": url,
                        "platform": search_keyword,
                    })
                    
        except Exception:
            logger.debug("Search failed for %s", search_keyword, exc_info=True)
        
        return results

    def _search_video_channels(self, company_name: str) -> list:
        """Search for video channels (Tencent Video, iQiyi, etc.)."""
        results = []
        
        video_keywords = ["视频号", "腾讯视频", "爱奇艺", "优酷"]
        
        for keyword in video_keywords:
            try:
                query = f"{company_name} {keyword}"
                resp = self._session.get(
                    BAIDU_SEARCH_URL, 
                    params={"wd": query}, 
                    timeout=15
                )
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")
                
                for item in soup.select(".result, .c-container"):
                    title_el = item.select_one(".t, h3")
                    if not title_el:
                        continue
                        
                    title = title_el.get_text(strip=True)
                    link_el = item.select_one("a")
                    if not link_el:
                        continue
                        
                    url = link_el.get("href", "")
                    
                    account_name = self._extract_account_name(title, url, keyword)
                    
                    if account_name:
                        results.append({
                            "account_name": account_name,
                            "url": url,
                            "platform": keyword,
                        })
                        
            except Exception:
                logger.debug("Failed to search video channel: %s", keyword, exc_info=True)
        
        return results

    def _extract_account_name(self, title: str, url: str, platform: str) -> str:
        """Extract account name from title or URL."""
        # Try to extract from URL first
        if "weixin.qq.com" in url:
            # WeChat official account
            match = re.search(r"gh_(\w+)", url)
            if match:
                return f"gh_{match.group(1)}"
        
        if "weibo.com" in url:
            # Weibo
            match = re.search(r"weibo.com/(\w+)", url)
            if match:
                return match.group(1)
        
        if "douyin.com" in url:
            # Douyin
            match = re.search(r"douyin.com/user/(\w+)", url)
            if match:
                return match.group(1)
        
        if "xiaohongshu.com" in url:
            # Xiaohongshu
            match = re.search(r"xiaohongshu.com/profile/(\w+)", url)
            if match:
                return match.group(1)
        
        # Try to extract from title
        # Remove platform keyword from title
        clean_title = title.replace(platform, "").strip()
        
        # Common patterns for account names
        patterns = [
            r"微信公众号[:：]?\s*([a-zA-Z0-9_\u4e00-\u9fa5]+)",
            r"微博[:：]?\s*(@[\w]+)",
            r"抖音[:：]?\s*([\w]+)",
            r"小红书[:：]?\s*([\w]+)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, clean_title, re.IGNORECASE)
            if match:
                return match.group(1)
        
        # Return cleaned title as fallback
        return clean_title[:30] if clean_title else ""
