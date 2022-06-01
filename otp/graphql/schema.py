import graphene

from saleor.graphql.core.federation import build_federated_schema
from otp.graphql.mutations import RequestPasswordRecovery, SetPasswordByCode


class Mutation(graphene.ObjectType):
    request_password_recovery = RequestPasswordRecovery.Field()
    set_password_by_code = SetPasswordByCode.Field()


schema = build_federated_schema(mutation=Mutation)
