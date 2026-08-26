package main

import (
	"context"
)

type claimsKey struct{}

func withClaims(ctx context.Context, c *claims) context.Context {
	return context.WithValue(ctx, claimsKey{}, c)
}

func claimsFromContext(ctx context.Context) *claims {
	return ctx.Value(claimsKey{}).(*claims)
}
