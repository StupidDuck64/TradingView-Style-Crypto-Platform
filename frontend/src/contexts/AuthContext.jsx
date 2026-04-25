import React, { createContext, useContext, useState, useCallback } from "react";

const AuthContext = createContext();

// Mock user storage using localStorage
function getStoredUsers() {
  try {
    return JSON.parse(localStorage.getItem("app_users") || "[]");
  } catch {
    return [];
  }
}

function storeUsers(users) {
  try {
    localStorage.setItem("app_users", JSON.stringify(users));
  } catch {}
}

function getStoredSession() {
  try {
    const s = localStorage.getItem("app_session");
    return s ? JSON.parse(s) : null;
  } catch {
    return null;
  }
}

function storeSession(session) {
  try {
    if (session) localStorage.setItem("app_session", JSON.stringify(session));
    else localStorage.removeItem("app_session");
  } catch {}
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => getStoredSession());

  const login = useCallback((email, password) => {
    const users = getStoredUsers();
    const found = users.find(
      (u) => u.email === email && u.password === password,
    );
    if (!found) return { success: false, error: "Invalid email or password" };
    const session = { email: found.email, name: found.name };
    setUser(session);
    storeSession(session);
    return { success: true };
  }, []);

  const register = useCallback((name, email, password) => {
    const users = getStoredUsers();
    if (users.find((u) => u.email === email)) {
      return { success: false, error: "Email already exists" };
    }
    users.push({ name, email, password });
    storeUsers(users);
    const session = { email, name };
    setUser(session);
    storeSession(session);
    return { success: true };
  }, []);

  const logout = useCallback(() => {
    setUser(null);
    storeSession(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
