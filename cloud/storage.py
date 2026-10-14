"""Private Supabase Storage; service credentials remain on the server."""
import json
import re
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

class Storage:
    def __init__(self, url, key, bucket='huanchang-media'):
        origin = urlsplit(url)
        if origin.scheme != 'https' or not origin.hostname or origin.path not in ['', '/'] or origin.query or origin.fragment or origin.username:
            raise ValueError('Storage requires an HTTPS project origin')
        if not re.fullmatch(r'[a-z0-9-]+', bucket) or not key:raise ValueError('Storage configuration missing')
        self.origin = url.rstrip('/');self.base = self.origin + '/storage/v1';self.key = key;self.bucket = bucket
    def call(self, path, data=None, method=None, headers=None, maximum=4096):
        head = {'Authorization': 'Bearer ' + self.key, 'apikey': self.key, **(headers or {})}
        if data is not None:head['Content-Type'] = 'application/json';data = json.dumps(data).encode()
        with urlopen(Request(self.base + path, data=data, method=method, headers=head), timeout=12) as response:
            body = response.read(maximum + 1)
            if len(body) > maximum:raise ValueError('Unexpected storage response size')
            return body, dict(response.headers)
    def signed(self, file_id, upload=False):
        if not re.fullmatch('[a-f0-9]{24}', file_id):raise ValueError('Invalid object key')
        path = '/object/' + ('upload/sign/' if upload else 'sign/') + self.bucket + '/' + file_id
        body, _ = self.call(path, {} if upload else {'expiresIn': 60}, method='POST')
        value = json.loads(body);relative = value.get('url') or value.get('signedURL') or value.get('signedUrl')
        if not isinstance(relative, str) or not relative.startswith('/object/'):raise ValueError('Invalid signed storage URL')
        return self.base + relative
    def inspect(self, file_id):
        body, headers = self.call('/object/authenticated/' + self.bucket + '/' + file_id, method='GET', headers={'Range': 'bytes=0-31'}, maximum=32)
        size = headers.get('Content-Range', headers.get('content-range', '')).rsplit('/', 1)[-1]
        if not size.isdigit():raise ValueError('Storage did not confirm object size')
        return body, int(size)
