import datetime

from livekit import api

from worker import config


class AccessTokenIssuer:
    """Signs LiveKit room tokens, so the API secret never reaches the browser."""

    def __init__(self):
        self.api_key = config.LIVEKIT_API_KEY
        self.api_secret = config.LIVEKIT_API_SECRET

    def issue_token(self, room_name, participant_identity, attributes):
        """attributes travel with the participant; the agent reads the conversation language from them."""
        access_token = api.AccessToken(self.api_key, self.api_secret)
        access_token.with_identity(participant_identity)
        access_token.with_attributes(attributes)
        access_token.with_ttl(datetime.timedelta(minutes=config.VOICE_TOKEN_LIFETIME_MINUTES))
        # can_update_own_metadata: the page changes its voice attribute during a call
        access_token.with_grants(api.VideoGrants(room_join=True, room=room_name, can_update_own_metadata=True))
        return access_token.to_jwt()
