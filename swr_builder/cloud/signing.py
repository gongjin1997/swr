"""华为官方 SDK-HMAC-SHA256 签名封装；不复用 SWR 登录密钥算法。"""
from datetime import datetime, timezone
from urllib.parse import urlsplit
from huaweicloudsdkcore.auth.credentials import BasicCredentials
from huaweicloudsdkcore.sdk_request import SdkRequest
from huaweicloudsdkcore.signer.signer import Signer


def sign_aksk(method, url, body, ak, sk, headers=None, timestamp=None):
    """对最终发送字节签名。只返回请求头，禁止记录含 Authorization 的结果。"""
    if not isinstance(body,bytes): raise ValueError('签名 body 必须是最终发送的 bytes')
    u = urlsplit(url)
    if u.query or u.fragment: raise ValueError('Token 签名 URL 不支持 query 或 fragment')
    h = dict(headers or {})
    if any(k.lower()=='authorization' for k in h): raise ValueError('不能传入已有 Authorization')
    h['Host'] = u.netloc
    h['Content-Type'] = 'application/json'
    h['Content-Length'] = str(len(body))
    h['X-Sdk-Date'] = timestamp or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    req = SdkRequest(method=method.upper(),schema=u.scheme,host=u.netloc,resource_path=u.path or '/',
        uri=u.path or '/',query_params=[],header_params=h,body=body)
    # 此处只使用签名器，不使用会自动发现项目或调用其他云端接口的完整 SDK Client。
    Signer(BasicCredentials(ak=ak,sk=sk)).sign(req)
    return req.header_params
