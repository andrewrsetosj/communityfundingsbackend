-- ============================================================
-- Community Fundings — Test Data (Fake Data for Development)
-- Inserts 10 fake creators, 10 fake campaigns, and associated data.
-- Run this AFTER running schema.sql to populate the database.
-- ============================================================

BEGIN;

-- ----------------------------
-- 1) Fake Creators (users + orgs)
-- ----------------------------
INSERT INTO creators (creator_id, user_type, name, last_name, email, bio, time_creation) VALUES
('creator_001', 1, 'Alice', 'Johnson', 'alice.johnson@example.com', 'Passionate indie game developer with 5 years of experience creating immersive worlds.', '2023-01-15T10:00:00Z'),
('creator_002', 1, 'Bob', 'Smith', 'bob.smith@example.com', 'Environmental activist and photographer capturing the beauty of nature.', '2023-02-20T14:30:00Z'),
('creator_003', 0, 'GreenTech Innovations', NULL, 'contact@greentech.com', 'A non-profit organization focused on sustainable technology solutions for communities.', '2023-03-10T09:15:00Z'),
('creator_004', 1, 'Charlie', 'Brown', 'charlie.brown@example.com', 'Aspiring novelist writing stories about human connection in the digital age.', '2023-04-05T16:45:00Z'),
('creator_005', 1, 'Diana', 'Williams', 'diana.williams@example.com', 'Chef and food blogger sharing recipes from around the world.', '2023-05-12T11:20:00Z'),
('creator_006', 0, 'Artisan Collective', NULL, 'info@artisancollective.org', 'A collective of local artisans promoting handmade crafts and traditional techniques.', '2023-06-18T13:00:00Z'),
('creator_007', 1, 'Ethan', 'Davis', 'ethan.davis@example.com', 'Software engineer building tools to make coding more accessible for beginners.', '2023-07-22T08:30:00Z'),
('creator_008', 1, 'Fiona', 'Garcia', 'fiona.garcia@example.com', 'Fitness instructor helping people achieve their health goals through personalized training.', '2023-08-14T15:10:00Z'),
('creator_009', 0, 'EduFuture Foundation', NULL, 'hello@edufuture.org', 'Foundation dedicated to improving education access in underserved communities.', '2023-09-09T12:45:00Z'),
('creator_010', 1, 'George', 'Miller', 'george.miller@example.com', 'Documentary filmmaker exploring social issues and human stories.', '2023-10-01T17:00:00Z');

-- ----------------------------
-- 2) Fake Campaigns
-- ----------------------------
INSERT INTO campaigns (campaign_id, creator_id, title, status, time_created, url, description_html, category, location, funding_goal_cents, duration_days, amount_raised_cents, backers, end_date, bio) VALUES
(1, 'creator_001', 'Epic Fantasy RPG Game', 'active', '2023-11-01T10:00:00Z', 'epic-fantasy-rpg', '<p>Join me in creating an immersive fantasy RPG with rich storytelling and beautiful pixel art.</p>', 'Games', 'San Francisco, CA', 5000000, 60, 1250000, 250, '2024-01-01T10:00:00Z', 'Indie developer passionate about creating games that tell meaningful stories.'),
(2, 'creator_002', 'Wildlife Photography Book', 'active', '2023-11-05T14:00:00Z', 'wildlife-photography-book', '<p>A coffee table book featuring stunning wildlife photography from around the world.</p>', 'Photography', 'Vancouver, Canada', 3000000, 45, 800000, 160, '2023-12-20T14:00:00Z', 'Nature photographer capturing the beauty and fragility of our planet.'),
(3, 'creator_003', 'Solar-Powered Community Center', 'active', '2023-11-10T09:00:00Z', 'solar-community-center', '<p>Building a sustainable community center powered entirely by solar energy.</p>', 'Environment', 'Austin, TX', 10000000, 90, 3500000, 700, '2024-02-08T09:00:00Z', 'Non-profit focused on sustainable solutions for community development.'),
(4, 'creator_004', 'Coming of Age Novel', 'active', '2023-11-15T16:00:00Z', 'coming-of-age-novel', '<p>A young adult novel about self-discovery and friendship in the age of social media.</p>', 'Writing', 'New York, NY', 2000000, 30, 450000, 90, '2023-12-15T16:00:00Z', 'Writer exploring themes of identity and connection in modern society.'),
(5, 'creator_005', 'Global Recipe App', 'active', '2023-11-20T11:00:00Z', 'global-recipe-app', '<p>An app connecting food lovers with authentic recipes from cultures around the world.</p>', 'Technology', 'London, UK', 4000000, 60, 1200000, 240, '2024-01-19T11:00:00Z', 'Chef passionate about sharing culinary traditions and fostering cultural exchange.'),
(6, 'creator_006', 'Handcrafted Jewelry Line', 'active', '2023-11-25T13:00:00Z', 'handcrafted-jewelry', '<p>Unique, ethically sourced jewelry pieces made by skilled artisans.</p>', 'Art', 'Portland, OR', 1500000, 45, 600000, 120, '2024-01-09T13:00:00Z', 'Collective of artisans preserving traditional craftsmanship techniques.'),
(7, 'creator_007', 'Coding Education Platform', 'active', '2023-11-30T08:00:00Z', 'coding-edu-platform', '<p>Interactive platform making programming education fun and accessible for all ages.</p>', 'Education', 'Seattle, WA', 6000000, 75, 2100000, 420, '2024-02-13T08:00:00Z', 'Engineer committed to democratizing access to technology education.'),
(8, 'creator_008', 'Virtual Fitness Classes', 'active', '2023-12-05T15:00:00Z', 'virtual-fitness-classes', '<p>Live and on-demand fitness classes accessible from anywhere in the world.</p>', 'Health', 'Los Angeles, CA', 2500000, 50, 950000, 190, '2024-01-24T15:00:00Z', 'Fitness expert helping people stay healthy and active regardless of location.'),
(9, 'creator_009', 'STEM Learning Kits', 'active', '2023-12-10T12:00:00Z', 'stem-learning-kits', '<p>Hands-on STEM education kits for schools in underserved communities.</p>', 'Education', 'Chicago, IL', 3500000, 60, 1400000, 280, '2024-02-08T12:00:00Z', 'Foundation bridging the education gap through innovative learning tools.'),
(10, 'creator_010', 'Social Justice Documentary', 'active', '2023-12-15T17:00:00Z', 'social-justice-doc', '<p>A documentary series exploring social justice issues and community activism.</p>', 'Film', 'Atlanta, GA', 4500000, 70, 1800000, 360, '2024-02-23T17:00:00Z', 'Filmmaker dedicated to amplifying marginalized voices and driving change.');

-- ----------------------------
-- 3) Fake FAQs for Campaigns
-- ----------------------------
INSERT INTO faqs (campaign_id, display_order, question, answer) VALUES
(1, 0, 'What platforms will the game be available on?', 'The game will launch on PC, Mac, and Linux, with potential mobile ports later.'),
(1, 1, 'Will there be multiplayer features?', 'Yes, we plan to include cooperative multiplayer modes in future updates.'),
(2, 0, 'What animals are featured in the book?', 'The book includes photography of wildlife from Africa, Asia, and the Americas.'),
(2, 1, 'Will prints be available for purchase?', 'Yes, limited edition prints will be available through our website.'),
(3, 0, 'How will the solar panels be maintained?', 'We have partnered with local experts for ongoing maintenance and monitoring.'),
(3, 1, 'Can the community center be used by the public?', 'Yes, it will be open to the community with various programs and events.'),
(4, 0, 'What age group is the novel targeted at?', 'The novel is primarily for young adults (ages 14-18) but appeals to all ages.'),
(4, 1, 'Will there be a sequel?', 'We have plans for a sequel if the first book is successful.'),
(5, 0, 'Will the app be available in multiple languages?', 'Yes, we plan to support multiple languages based on user demand.'),
(5, 1, 'How do you ensure recipe authenticity?', 'All recipes are contributed by native chefs and verified for authenticity.'),
(6, 0, 'What materials are used in the jewelry?', 'We use ethically sourced silver, gold, and gemstones from sustainable suppliers.'),
(6, 1, 'Do you offer custom pieces?', 'Yes, custom orders are available with additional lead time.'),
(7, 0, 'What programming languages are taught?', 'We start with Python and JavaScript, expanding to more languages over time.'),
(7, 1, 'Is the platform free for students?', 'Basic access is free, with premium features available for a small fee.'),
(8, 0, 'What types of classes are offered?', 'We offer yoga, strength training, cardio, and specialized classes like prenatal fitness.'),
(8, 1, 'Can I get a refund if I miss a class?', 'Refunds are available up to 24 hours before class time.'),
(9, 0, 'Which grade levels do the kits support?', 'The kits are designed for elementary through high school students.'),
(9, 1, 'Do you provide teacher training?', 'Yes, we offer professional development workshops for educators.'),
(10, 0, 'How long will the documentary series be?', 'The series will consist of 6-8 episodes, each 20-30 minutes long.'),
(10, 1, 'Will the documentary be available for streaming?', 'Yes, it will be available on major streaming platforms after release.');

-- ----------------------------
-- 4) Fake Rewards for Campaigns
-- ----------------------------
INSERT INTO rewards (campaign_id, title, required_amount_cents, description, limit_total, display_order) VALUES
(1, 'Digital Game Copy', 1500, 'Get a digital copy of the game upon release.', NULL, 0),
(1, 'Early Access Beta', 3000, 'Play the game early and provide feedback to developers.', 100, 1),
(1, 'Art Book + Digital Copy', 5000, 'Physical art book featuring game concept art plus digital game copy.', 50, 2),
(2, 'Digital Book Copy', 2500, 'High-resolution PDF of the photography book.', NULL, 0),
(2, 'Limited Edition Print', 7500, '8x10 signed print of your choice from the book.', 25, 1),
(2, 'Book + Print Bundle', 10000, 'Physical book plus a limited edition print.', 20, 2),
(3, 'Solar Panel Kit', 5000, 'DIY solar panel kit for your home.', NULL, 0),
(3, 'Community Center Membership', 10000, 'Annual membership to the community center with event access.', 200, 1),
(3, 'Naming Rights', 25000, 'Have a room or area named after you in the community center.', 5, 2),
(4, 'Digital Book Copy', 1000, 'E-book version of the novel.', NULL, 0),
(4, 'Signed Physical Copy', 2500, 'Autographed hardcover copy of the book.', 100, 1),
(4, 'Author Q&A Session', 5000, 'Virtual Q&A session with the author.', 20, 2),
(5, 'App Access', 500, 'Lifetime access to the recipe app.', NULL, 0),
(5, 'Premium Subscription', 2000, 'One year of premium features including exclusive recipes.', NULL, 1),
(5, 'Cooking Class', 5000, 'Virtual cooking class with the chef.', 30, 2),
(6, 'Necklace', 3000, 'Handcrafted silver necklace.', NULL, 0),
(6, 'Earrings + Necklace Set', 6000, 'Matching earrings and necklace set.', 50, 1),
(6, 'Custom Design Consultation', 10000, 'One-hour consultation for custom jewelry design.', 10, 2),
(7, 'Basic Course Access', 1000, 'Access to introductory programming courses.', NULL, 0),
(7, 'Full Platform Access', 5000, 'Access to all courses and premium features for one year.', NULL, 1),
(7, 'School License', 10000, 'License for an entire classroom (up to 30 students).', 50, 2),
(8, 'Single Class Pass', 1500, 'Access to one live fitness class.', NULL, 0),
(8, 'Monthly Subscription', 5000, 'One month of unlimited access to all classes.', NULL, 1),
(8, 'Personal Training Session', 10000, 'One-on-one virtual personal training session.', 25, 2),
(9, 'Single STEM Kit', 2000, 'One complete STEM learning kit.', NULL, 0),
(9, 'Classroom Set', 5000, 'Enough kits for a classroom of 25 students.', 40, 1),
(9, 'School Partnership', 15000, 'Partnership package including kits and teacher training.', 10, 2),
(10, 'Digital Access', 1000, 'Stream all episodes online.', NULL, 0),
(10, 'DVD Set', 3000, 'Physical DVD set of the complete series.', 100, 1),
(10, 'Behind-the-Scenes Access', 7500, 'Exclusive behind-the-scenes content and Q&A with filmmakers.', 30, 2);

-- ----------------------------
-- 5) Fake Collaborators for Campaigns
-- ----------------------------
INSERT INTO collaborators (campaign_id, email, status, time_created) VALUES
(1, 'dev.partner@example.com', 'accepted', '2023-11-02T10:00:00Z'),
(1, 'artist.collab@example.com', 'pending', '2023-11-03T10:00:00Z'),
(2, 'editor@example.com', 'accepted', '2023-11-06T14:00:00Z'),
(3, 'architect@example.com', 'accepted', '2023-11-11T09:00:00Z'),
(3, 'engineer@example.com', 'accepted', '2023-11-12T09:00:00Z'),
(4, 'beta.reader@example.com', 'accepted', '2023-11-16T16:00:00Z'),
(5, 'translator@example.com', 'pending', '2023-11-21T11:00:00Z'),
(6, 'jeweler@example.com', 'accepted', '2023-11-26T13:00:00Z'),
(7, 'educator@example.com', 'accepted', '2023-12-01T08:00:00Z'),
(8, 'nutritionist@example.com', 'pending', '2023-12-06T15:00:00Z'),
(9, 'scientist@example.com', 'accepted', '2023-12-11T12:00:00Z'),
(10, 'producer@example.com', 'accepted', '2023-12-16T17:00:00Z');

-- ----------------------------
-- 6) Fake Bank Details for Campaigns
-- ----------------------------
-- After migration 002: (campaign_id, fermat_key, account_type) — fermat_key holds Fernet ciphertext in prod.
-- Legacy seed used plaintext routing/account; replace fermat_key with real tokens if testing encryption.
INSERT INTO bank_details (campaign_id, fermat_key, account_type) VALUES
(1, '121000248', 'individual'),
(2, '021000021', 'individual'),
(3, '111000025', 'business'),
(4, '031100173', 'individual'),
(5, '051000017', 'individual'),
(6, '101000187', 'business'),
(7, '071000013', 'individual'),
(8, '081000210', 'individual'),
(9, '091000022', 'business'),
(10, '011000015', 'individual');

COMMIT;