package main

import (
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"math/big"
	"net/http"
	"strings"
	"sync"
	"time"
)

const defaultOIDCIssuer = "https://passport-ai.ouchn.edu.cn"

type jwksVerifier struct {
	url    string
	client *http.Client

	mu        sync.Mutex
	keys      map[string]*rsa.PublicKey
	fetchedAt time.Time
}

type jwksDocument struct {
	Keys []jwk `json:"keys"`
}

type jwk struct {
	KTY string `json:"kty"`
	Use string `json:"use"`
	KID string `json:"kid"`
	Alg string `json:"alg"`
	N   string `json:"n"`
	E   string `json:"e"`
}

func newJWKSVerifier(url string) *jwksVerifier {
	return &jwksVerifier{
		url:    url,
		client: &http.Client{Timeout: 10 * time.Second},
		keys:   make(map[string]*rsa.PublicKey),
	}
}

func (v *jwksVerifier) key(kid string) (*rsa.PublicKey, error) {
	v.mu.Lock()
	defer v.mu.Unlock()
	if key := v.keys[kid]; key != nil && time.Since(v.fetchedAt) < 10*time.Minute {
		return key, nil
	}
	if err := v.refreshLocked(); err != nil {
		return nil, err
	}
	key := v.keys[kid]
	if key == nil {
		return nil, fmt.Errorf("JWKS does not contain key id %q", kid)
	}
	return key, nil
}

func (v *jwksVerifier) refreshLocked() error {
	resp, err := v.client.Get(v.url)
	if err != nil {
		return fmt.Errorf("fetch JWKS: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("fetch JWKS: status %d", resp.StatusCode)
	}
	var doc jwksDocument
	if err := json.NewDecoder(resp.Body).Decode(&doc); err != nil {
		return fmt.Errorf("decode JWKS: %w", err)
	}
	keys := make(map[string]*rsa.PublicKey)
	for _, item := range doc.Keys {
		if item.KTY != "RSA" || item.KID == "" || (item.Use != "" && item.Use != "sig") || (item.Alg != "" && item.Alg != "RS256") {
			continue
		}
		n, err := base64.RawURLEncoding.DecodeString(item.N)
		if err != nil {
			return fmt.Errorf("decode JWKS modulus: %w", err)
		}
		e, err := base64.RawURLEncoding.DecodeString(item.E)
		if err != nil {
			return fmt.Errorf("decode JWKS exponent: %w", err)
		}
		exponent := new(big.Int).SetBytes(e)
		if !exponent.IsInt64() || exponent.Int64() < 3 {
			return fmt.Errorf("invalid JWKS exponent")
		}
		keys[item.KID] = &rsa.PublicKey{N: new(big.Int).SetBytes(n), E: int(exponent.Int64())}
	}
	if len(keys) == 0 {
		return fmt.Errorf("JWKS contains no usable RS256 keys")
	}
	v.keys = keys
	v.fetchedAt = time.Now()
	return nil
}

func rolesFromClaims(roles []string, role json.RawMessage) []string {
	if len(roles) > 0 {
		return roles
	}
	var one string
	if json.Unmarshal(role, &one) == nil && one != "" {
		return strings.FieldsFunc(one, func(r rune) bool { return r == ',' || r == ' ' })
	}
	var many []string
	if json.Unmarshal(role, &many) == nil {
		return many
	}
	return nil
}
