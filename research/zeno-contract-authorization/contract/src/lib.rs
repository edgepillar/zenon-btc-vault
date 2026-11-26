//! A one-shot authorization for an isolated Zeno devnet experiment.
#![no_std]

use zeno_contract::prelude::*;

const AUTHORIZER: Caller = Caller::new(*include_bytes!("caller.bin"));

#[contract]
mod vault_authorizer {
    use super::*;

    #[storage(0x01)]
    const COMMITMENT: Item<Hash32> = Item::new();

    #[contract_error]
    pub enum AuthorizationError {
        Unauthorized = 1,
        AlreadyAuthorized = 2,
    }

    #[method]
    pub fn authorize(ctx: &mut Ctx, commitment: Hash32) -> Result<(), AuthorizationError> {
        if ctx.caller() != AUTHORIZER {
            return Err(AuthorizationError::Unauthorized);
        }
        if COMMITMENT.may_load(ctx).is_some() {
            return Err(AuthorizationError::AlreadyAuthorized);
        }
        COMMITMENT.save(ctx, &commitment);
        Ok(())
    }
}
