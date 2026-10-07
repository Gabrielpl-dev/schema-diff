CREATE TABLE users (
  id SERIAL PRIMARY KEY,
  email VARCHAR(200) NOT NULL,
  created_at TIMESTAMPTZ
);

CREATE TABLE posts (
  id INT PRIMARY KEY,
  user_id INT,
  FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE comments (
  id INT PRIMARY KEY,
  post_id INT,
  FOREIGN KEY (post_id) REFERENCES posts (id)
);

CREATE INDEX idx_users_email ON users (email);
