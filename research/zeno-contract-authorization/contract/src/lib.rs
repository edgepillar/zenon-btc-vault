//! A one-shot authorization for an isolated Zeno devnet experiment.
#![no_std]

use zeno_contract::prelude::*;

const AUTHORIZER: Caller = Caller::new(*include_bytes!("caller.bin"));

#[contract]
mod vault_authorizer {
    use super::*;

    /// The single Bitcoin target commitment stored by this instance.
    #[storage(0x01)]
    const COMMITMENT: Item<Hash32> = Item::new();

    #[contract_error]
    pub enum AuthorizationError {
        /// The caller differs from the configured devnet authorizer.
        Unauthorized = 1,
        /// A commitment has already been stored.
        AlreadyAuthorized = 2,
    }

    /// Store the authorized commitment once for the configured caller.
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
