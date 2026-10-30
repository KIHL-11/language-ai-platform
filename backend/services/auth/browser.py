"""Explicit, fail-closed browser-session bootstrap and same-origin boundary."""
import os
import secrets
import threading

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from services.auth.identity import DeploymentMode, IdentityDependency, InvalidAuthentication, deployment_mode
from services.auth.sessions import (
    ABSOLUTE_LIFETIME, COOKIE_NAME, BrowserSessionRepository,
    BrowserSessionVerifier, SessionPolicy, SessionUnavailable, csrf_token,
)

router=APIRouter()
_bootstrap_lock=threading.Lock()


@router.get('/deployment')
def deployment_metadata(response:Response):
    response.headers['Cache-Control']='no-store'
    return {'mode':deployment_mode().value}


def configure_browser_sessions(app, repository, policy):
    """Trusted bootstrap hook, also used by isolated tests. No HTTP injection."""
    if not isinstance(repository,BrowserSessionRepository) or not isinstance(policy,SessionPolicy):
        raise ValueError('Invalid browser session configuration')
    app.state.browser_session_repository=repository
    app.state.browser_session_policy=policy
    app.state.identity_verifier=BrowserSessionVerifier(repository)


def _bootstrap(app):
    with _bootstrap_lock:
        if getattr(app.state,'browser_session_policy',None) is not None: return
        dsn=os.getenv('CLOUD_SESSION_DATABASE_URL','').strip()
        origin=os.getenv('CLOUD_SESSION_ORIGIN','').strip()
        if not dsn and not origin: return
        if not dsn or not origin: raise SessionUnavailable('Incomplete cloud session configuration')
        configure_browser_sessions(app,BrowserSessionRepository(dsn),SessionPolicy(origin))


class BrowserSessionBoundary(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        try:
            if deployment_mode() is not DeploymentMode.CLOUD:
                return await call_next(request)
            _bootstrap(request.app)
            policy=getattr(request.app.state,'browser_session_policy',None)
            if policy is None:
                return await call_next(request)  # existing protected routes remain fail closed
            # Trust the ASGI transport only. The server must disable untrusted
            # proxy header processing; no browser header can establish HTTPS.
            if request.url.scheme != 'https' or f'{request.url.scheme}://{request.headers.get("host","")}' != policy.origin:
                return JSONResponse({'detail':{'code':'session_transport_invalid'}},status_code=403)
            origin=request.headers.get('origin')
            if origin is not None and origin != policy.origin:
                return JSONResponse({'detail':{'code':'origin_rejected'}},status_code=403)
            if request.headers.get('sec-fetch-site') in ('cross-site','same-site'):
                return JSONResponse({'detail':{'code':'cross_site_rejected'}},status_code=403)
            if request.method not in ('GET','HEAD','OPTIONS'):
                if request.url.path == '/api/account/login':
                    return JSONResponse({'detail':{'code':'login_provider_not_configured'}},status_code=503)
                if origin != policy.origin:
                    return JSONResponse({'detail':{'code':'origin_required'}},status_code=403)
                token=request.cookies.get(COOKIE_NAME,'')
                principal=await run_in_threadpool(request.app.state.identity_verifier,request)
                supplied=request.headers.get('x-csrf-token','')
                if not supplied or len(supplied)!=64 or not secrets.compare_digest(supplied,csrf_token(token)):
                    return JSONResponse({'detail':{'code':'csrf_invalid'}},status_code=403)
                request.state.browser_principal=principal
            response=await call_next(request)
            owner=getattr(request.state,'authenticated_user_id',None)
            if owner is not None:
                response.headers['X-Authenticated-User']=owner
                response.headers['Cache-Control']='private, no-store'
            if request.url.path.startswith('/api/account/'):
                response.headers['Cache-Control']='no-store'
            return response
        except InvalidAuthentication:
            return JSONResponse({'detail':{'code':'authentication_invalid'}},status_code=401)
        except (SessionUnavailable,ValueError):
            return JSONResponse({'detail':{'code':'cloud_session_unavailable'}},status_code=503)
        except HTTPException as exc:
            return JSONResponse({'detail':exc.detail},status_code=exc.status_code)


def _repository(request):
    if deployment_mode() is not DeploymentMode.CLOUD:
        raise HTTPException(status_code=503,detail='Cloud accounts are unavailable in Personal Desktop')
    repository=getattr(request.app.state,'browser_session_repository',None)
    if not isinstance(repository,BrowserSessionRepository):
        raise HTTPException(status_code=503,detail='Cloud sessions are not configured')
    return repository


@router.post('/account/login')
def login_unavailable():
    raise HTTPException(status_code=503,detail='No verified external login provider is configured')


@router.get('/account/me')
def account_me(request:Request,identity:IdentityDependency):
    _repository(request)
    return {'user_id':identity.authenticated_user_id,'csrf_token':csrf_token(request.cookies.get(COOKIE_NAME,''))}


@router.post('/account/session/rotate')
def rotate_session(request:Request,response:Response,identity:IdentityDependency):
    repository=_repository(request)
    try: token=repository.rotate(request.cookies.get(COOKIE_NAME,''))
    except InvalidAuthentication as exc: raise HTTPException(status_code=401,detail='Invalid session') from exc
    except SessionUnavailable as exc: raise HTTPException(status_code=503,detail='Session unavailable') from exc
    response.set_cookie(COOKIE_NAME,token,secure=True,httponly=True,samesite='strict',path='/',max_age=int(ABSOLUTE_LIFETIME.total_seconds()))
    return {'csrf_token':csrf_token(token)}


@router.post('/account/logout',status_code=204)
def logout(request:Request,identity:IdentityDependency):
    repository=_repository(request)
    try: repository.revoke(request.cookies.get(COOKIE_NAME,''))
    except InvalidAuthentication as exc: raise HTTPException(status_code=401,detail='Invalid session') from exc
    except SessionUnavailable as exc: raise HTTPException(status_code=503,detail='Session unavailable') from exc
    response=Response(status_code=204)
    response.delete_cookie(COOKIE_NAME,path='/',secure=True,httponly=True,samesite='strict')
    return response
