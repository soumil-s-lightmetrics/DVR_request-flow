from utils.auth_util import AuthManager

auth_manager = AuthManager()

def get_headers():
    auth_token, id_token = auth_manager._get_access_token()
    return {
        'Authorization': f"Bearer {auth_token}",
        'id-token': id_token,
        'x-lm-desired-account': 'lmpresales'
    }