import graphene
from graphene_federation import build_schema

from .mutations import RequestPasswordRecovery, SetPasswordByCode


class Mutation(graphene.ObjectType):
    request_password_recovery = RequestPasswordRecovery.Field()
    set_password_by_code = SetPasswordByCode.Field()


schema = build_schema(mutation=Mutation)
