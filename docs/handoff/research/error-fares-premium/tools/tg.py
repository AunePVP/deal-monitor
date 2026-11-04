import sys,re,html
p=sys.argv[1]; n=int(sys.argv[2]) if len(sys.argv)>2 else 12
h=open(p,encoding='utf-8',errors='replace').read()
title=re.search(r'<title>([^<]*)',h); print("TITLE:",title.group(1) if title else None)
desc=re.search(r'class="tgme_channel_info_description">(.*?)</div>',h,re.S); 
if desc: print("DESC:",re.sub('<[^>]+>','',desc.group(1))[:300])
cnt=re.search(r'tgme_channel_info_counter[^>]*>\s*<span class="counter_value">([^<]+)</span>\s*<span class="counter_type">([^<]+)',h)
if cnt: print("COUNTER:",cnt.group(1),cnt.group(2))
msgs=re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*(?:<div class="tgme_widget_message_(?:link_preview|photo|footer|inline)|<a class="tgme_widget_message_link_preview)',h,re.S)
if not msgs:
    msgs=re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',h,re.S)
dates=re.findall(r'<time datetime="([^"]+)"',h)
print("MESSAGES:",len(msgs),"DATES:",len(dates), "newest:", max(dates) if dates else None, "oldest:", min(dates) if dates else None)
for i,m in enumerate(msgs[-n:]):
    t=html.unescape(re.sub(r'<br\s*/?>','\n',m)); t=re.sub('<[^>]+>','',t).strip().replace('\n',' | ')
    print("-",t[:220])
