CREATE TABLE users (id INT PRIMARY KEY, email VARCHAR(100));
CREATE TABLE posts (id INT PRIMARY KEY, user_id INT, FOREIGN KEY (user_id) REFERENCES users (id));
CREATE TABLE comments (id INT PRIMARY KEY, post_id INT, FOREIGN KEY (post_id) REFERENCES posts (id));
