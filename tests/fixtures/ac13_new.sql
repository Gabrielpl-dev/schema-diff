CREATE TABLE users (id INT PRIMARY KEY, email VARCHAR(100));
CREATE INDEX idx_users_email ON users (email);
